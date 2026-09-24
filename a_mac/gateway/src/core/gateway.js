import { EventEmitter } from "node:events";
import { GatewayError, PlatformSendError, asGatewayError } from "./errors.js";
import { inboundDedupeKey, newId, stableSessionKey } from "./ids.js";
import { normalizeOutboundRequest, normalizeTrustedInbound } from "./validation.js";

function errorRecord(error) {
  const converted = asGatewayError(error);
  return {
    code: converted.code,
    message: converted.message,
    retryable: Boolean(converted.retryable),
    ambiguous: Boolean(converted.ambiguous),
    details: converted.details,
  };
}

function canonicalize(value) {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value).sort().map((key) => [key, canonicalize(value[key])]),
    );
  }
  return value;
}

function sameRequest(left, right) {
  return JSON.stringify(canonicalize(left)) === JSON.stringify(canonicalize(right));
}

export class ChannelGateway extends EventEmitter {
  constructor({ config, registry, store, logger }) {
    super();
    this.config = config;
    this.registry = registry;
    this.store = store;
    this.logger = logger;
  }

  resolveAccount(channelId, accountId = "default") {
    const adapter = this.registry.get(channelId);
    const channelConfig = this.config.channels?.[adapter.id];
    if (!channelConfig || channelConfig.enabled === false) {
      throw new GatewayError("CHANNEL_DISABLED", `Channel is disabled: ${adapter.id}`, { status: 404 });
    }
    const accountConfig = channelConfig.accounts?.[accountId];
    if (!accountConfig) {
      throw new GatewayError("CHANNEL_ACCOUNT_NOT_FOUND", `Channel account not found: ${adapter.id}/${accountId}`, { status: 404 });
    }
    return { adapter, channelConfig, accountConfig };
  }

  enrichInbound(event) {
    const normalized = normalizeTrustedInbound(event);
    return {
      ...normalized,
      id: newId("evt"),
      receivedAt: new Date().toISOString(),
      session: {
        key: stableSessionKey({
          channel: normalized.channel,
          accountId: normalized.accountId,
          conversationId: normalized.conversation.id,
          threadId: normalized.conversation.threadId,
        }),
      },
    };
  }

  async ingestWebhook(channelId, accountId, request) {
    const { adapter, accountConfig } = this.resolveAccount(channelId, accountId);
    const received = await adapter.receive({
      accountId,
      accountConfig,
      headers: request.headers,
      rawBody: request.rawBody,
      body: request.body,
    });
    if (received.response) {
      return { response: received.response, accepted: [], ignored: false };
    }
    const accepted = [];
    for (const event of received.events ?? []) {
      const enriched = this.enrichInbound({ ...event, channel: adapter.id, accountId });
      const dedupeKey = inboundDedupeKey(enriched);
      const stored = await this.store.addInboundEvent(enriched, dedupeKey);
      accepted.push(stored);
      if (!stored.duplicate) this.emit("event", stored.record);
      this.logger.info("inbound event accepted", {
        event_id: stored.record.id,
        sequence: stored.record.sequence,
        channel: stored.record.channel,
        account_id: stored.record.accountId,
        duplicate: stored.duplicate,
        session_key: stored.record.session.key,
      });
    }
    return {
      accepted,
      ignored: Boolean(received.ignored),
      ignoredReason: received.ignoredReason,
    };
  }

  async ingestTrusted(input) {
    const normalized = normalizeTrustedInbound(input);
    const { adapter } = this.resolveAccount(normalized.channel, normalized.accountId);
    const enriched = this.enrichInbound({ ...normalized, channel: adapter.id });
    const dedupeKey = inboundDedupeKey(enriched);
    const stored = await this.store.addInboundEvent(enriched, dedupeKey);
    if (!stored.duplicate) this.emit("event", stored.record);
    return stored;
  }

  async sendMessage(input, options = {}) {
    const normalized = normalizeOutboundRequest(input);
    const { adapter, accountConfig } = this.resolveAccount(normalized.channel, normalized.accountId);
    const idempotencyKey = normalized.idempotencyKey ?? options.idempotencyKey ?? newId("idem");
    normalized.idempotencyKey = idempotencyKey;

    const created = await this.store.createOutboundIntent(normalized, idempotencyKey);
    let intent = created.intent;
    if (created.existing && !sameRequest(intent.request, normalized)) {
      throw new GatewayError("IDEMPOTENCY_CONFLICT", "The idempotency key is already associated with a different message request", {
        status: 409,
        details: { intent_id: intent.id },
      });
    }
    const prepared = await this.store.prepareOutboundSend(intent.id, { force: Boolean(options.force) });
    intent = prepared.intent;
    if (prepared.replayed) {
      const receipt = await this.store.getReceipt(intent.receiptId);
      return { intent, receipt, replayed: true };
    }

    this.emit("outbound", intent);
    try {
      const platform = await adapter.send({
        request: normalized,
        accountConfig,
        intentId: intent.id,
      });
      const completed = await this.store.completeOutbound(intent.id, {
        status: "sent",
        channel: adapter.id,
        accountId: normalized.accountId,
        intentId: intent.id,
        idempotencyKey,
        platformMessageId: platform.platformMessageId,
        conversationId: platform.conversationId ?? normalized.to.conversationId,
        createdAt: new Date().toISOString(),
        evidence: "platform_api_accepted",
        raw: platform.raw ?? null,
      });
      this.emit("receipt", completed.receipt);
      this.logger.info("outbound message accepted by platform", {
        intent_id: intent.id,
        receipt_id: completed.receipt.id,
        channel: adapter.id,
        account_id: normalized.accountId,
        platform_message_id: completed.receipt.platformMessageId,
      });
      return { ...completed, replayed: false };
    } catch (error) {
      const converted = error instanceof PlatformSendError ? error : asGatewayError(error);
      const unknown = converted.ambiguous || (error instanceof PlatformSendError && !error.safeToRetry);
      const failed = await this.store.failOutbound(intent.id, errorRecord(converted), { unknown });
      this.emit("outbound", failed);
      converted.details = {
        ...(converted.details ?? {}),
        intent_id: intent.id,
        outbound_status: failed.status,
      };
      if (unknown) {
        converted.ambiguous = true;
        converted.retryable = false;
      }
      throw converted;
    }
  }

  async replyToEvent(eventId, input, options = {}) {
    const event = await this.store.getEvent(eventId);
    if (!event) {
      throw new GatewayError("EVENT_NOT_FOUND", `Event not found: ${eventId}`, { status: 404 });
    }
    // 2026-08-19：透传 msgType + content，让 chat_reply 适配层可发送飞书 interactive 卡片。
    // 之前只透传 text，导致 LLM 输出的 markdown 在飞书 text 类型下被剥成纯文本（###/表格丢失）。
    return await this.sendMessage({
      channel: input.channel ?? event.channel,
      accountId: input.accountId ?? event.accountId,
      to: input.to ?? { conversationId: event.conversation.id },
      text: input.text,
      msgType: input.msgType ?? input.msg_type,
      content: input.content,
      replyToId: input.replyToId ?? event.message.id,
      threadId: input.threadId ?? event.conversation.threadId,
      metadata: { ...(input.metadata ?? {}), sourceEventId: eventId },
      idempotencyKey: input.idempotencyKey,
    }, options);
  }

  async retryOutboundIntent(intentId, { force = false } = {}) {
    const intent = await this.store.getOutboundIntent(intentId);
    if (!intent) {
      throw new GatewayError("OUTBOUND_INTENT_NOT_FOUND", `Outbound intent not found: ${intentId}`, { status: 404 });
    }
    return await this.sendMessage(intent.request, {
      force,
      idempotencyKey: intent.idempotencyKey,
    });
  }
}
