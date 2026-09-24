import { GatewayError } from "./errors.js";

export function requireObject(value, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new GatewayError("VALIDATION_ERROR", `${label} must be an object`, { status: 400 });
  }
  return value;
}

export function requireString(value, label, { allowEmpty = false, maxLength = 100_000 } = {}) {
  if (typeof value !== "string" || (!allowEmpty && value.trim() === "")) {
    throw new GatewayError("VALIDATION_ERROR", `${label} must be a non-empty string`, { status: 400 });
  }
  if (value.length > maxLength) {
    throw new GatewayError("VALIDATION_ERROR", `${label} exceeds ${maxLength} characters`, { status: 400 });
  }
  return value;
}

export function optionalString(value, label, options = {}) {
  if (value === undefined || value === null || value === "") return undefined;
  return requireString(value, label, options);
}

export function normalizeMedia(media) {
  if (media === undefined || media === null) return [];
  if (!Array.isArray(media)) {
    throw new GatewayError("VALIDATION_ERROR", "message.media must be an array", { status: 400 });
  }
  return media.map((item, index) => {
    requireObject(item, `message.media[${index}]`);
    return {
      kind: optionalString(item.kind, `message.media[${index}].kind`, { maxLength: 64 }) ?? "file",
      url: optionalString(item.url, `message.media[${index}].url`, { maxLength: 4096 }),
      contentType: optionalString(item.contentType ?? item.content_type, `message.media[${index}].contentType`, { maxLength: 255 }),
      name: optionalString(item.name, `message.media[${index}].name`, { maxLength: 512 }),
      platformId: optionalString(item.platformId ?? item.platform_id, `message.media[${index}].platformId`, { maxLength: 512 }),
      size: Number.isFinite(Number(item.size)) ? Number(item.size) : undefined,
    };
  });
}

export function normalizeOutboundRequest(input) {
  requireObject(input, "message request");
  const toInput = typeof input.to === "string" ? { conversationId: input.to } : requireObject(input.to, "to");
  const text = requireString(input.text, "text", { allowEmpty: false, maxLength: 100_000 });
  // msgType / content（2026-08-17 新增）：支持飞书交互式卡片 / 透传任意 msg_type。
  // 仅当 msgType === "interactive" 且 content 非空时，下游 adapter 才会真正用 content 作为飞书 message content；
  // 否则仍走 text 模式（content 被忽略）。
  const msgType = optionalString(input.msgType ?? input.msg_type, "msgType", { maxLength: 32 }) ?? "text";
  const content = optionalString(input.content, "content", { maxLength: 100_000 });
  return {
    channel: requireString(input.channel, "channel", { maxLength: 128 }).toLowerCase(),
    accountId: optionalString(input.accountId ?? input.account_id, "accountId", { maxLength: 256 }) ?? "default",
    to: {
      conversationId: requireString(toInput.conversationId ?? toInput.conversation_id ?? toInput.id, "to.conversationId", { maxLength: 1024 }),
      receiveIdType: optionalString(toInput.receiveIdType ?? toInput.receive_id_type, "to.receiveIdType", { maxLength: 64 }),
    },
    text,
    msgType,
    content,
    replyToId: optionalString(input.replyToId ?? input.reply_to_id, "replyToId", { maxLength: 1024 }),
    threadId: optionalString(input.threadId ?? input.thread_id, "threadId", { maxLength: 1024 }),
    metadata: input.metadata && typeof input.metadata === "object" && !Array.isArray(input.metadata) ? input.metadata : {},
    idempotencyKey: optionalString(input.idempotencyKey ?? input.idempotency_key, "idempotencyKey", { maxLength: 512 }),
  };
}

export function normalizeTrustedInbound(input) {
  requireObject(input, "inbound event");
  const sender = requireObject(input.sender, "sender");
  const conversation = requireObject(input.conversation, "conversation");
  const message = requireObject(input.message, "message");
  const channel = requireString(input.channel, "channel", { maxLength: 128 }).toLowerCase();
  const accountId = optionalString(input.accountId ?? input.account_id, "accountId", { maxLength: 256 }) ?? "default";
  return {
    platformEventId: optionalString(input.platformEventId ?? input.platform_event_id ?? input.eventId ?? input.event_id, "platformEventId", { maxLength: 1024 }),
    channel,
    accountId,
    kind: optionalString(input.kind, "kind", { maxLength: 64 }) ?? "message",
    occurredAt: optionalString(input.occurredAt ?? input.occurred_at, "occurredAt", { maxLength: 64 }) ?? new Date().toISOString(),
    sender: {
      id: requireString(sender.id, "sender.id", { maxLength: 1024 }),
      name: optionalString(sender.name, "sender.name", { maxLength: 512 }),
      type: optionalString(sender.type, "sender.type", { maxLength: 64 }) ?? "user",
    },
    conversation: {
      id: requireString(conversation.id, "conversation.id", { maxLength: 1024 }),
      type: optionalString(conversation.type, "conversation.type", { maxLength: 64 }) ?? "direct",
      threadId: optionalString(conversation.threadId ?? conversation.thread_id, "conversation.threadId", { maxLength: 1024 }),
      parentId: optionalString(conversation.parentId ?? conversation.parent_id, "conversation.parentId", { maxLength: 1024 }),
    },
    message: {
      id: optionalString(message.id, "message.id", { maxLength: 1024 }),
      type: optionalString(message.type, "message.type", { maxLength: 64 }) ?? "text",
      text: typeof message.text === "string" ? message.text : "",
      replyToId: optionalString(message.replyToId ?? message.reply_to_id, "message.replyToId", { maxLength: 1024 }),
      mentions: Array.isArray(message.mentions) ? message.mentions : [],
      media: normalizeMedia(message.media),
    },
    raw: input.raw ?? null,
    metadata: input.metadata && typeof input.metadata === "object" && !Array.isArray(input.metadata) ? input.metadata : {},
  };
}
