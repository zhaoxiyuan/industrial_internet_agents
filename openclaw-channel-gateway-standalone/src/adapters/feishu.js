import { GatewayError, PlatformSendError } from "../core/errors.js";
import { decryptFeishuPayload, constantTimeEqual, verifyFeishuSignature } from "../util/crypto.js";
import { fetchJson } from "../util/http.js";
import { normalizeTrustedInbound } from "../core/validation.js";

function domainBase(domain) {
  return domain === "lark" ? "https://open.larksuite.com" : "https://open.feishu.cn";
}


function parseFeishuTime(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) return new Date().toISOString();
  const milliseconds = numeric < 10_000_000_000 ? numeric * 1000 : numeric;
  const date = new Date(milliseconds);
  return Number.isNaN(date.getTime()) ? new Date().toISOString() : date.toISOString();
}

function redactFeishuPayload(payload) {
  const clone = structuredClone(payload);
  if (clone && typeof clone === "object") {
    if ("token" in clone) clone.token = "***redacted***";
    if (clone.header && typeof clone.header === "object" && "token" in clone.header) {
      clone.header.token = "***redacted***";
    }
  }
  return clone;
}

function parseTextContent(message) {
  if (message?.message_type !== "text") return "";
  try {
    const parsed = JSON.parse(message.content ?? "{}");
    return typeof parsed.text === "string" ? parsed.text : "";
  } catch {
    return "";
  }
}

function normalizeMentions(mentions) {
  if (!Array.isArray(mentions)) return [];
  return mentions.map((mention) => ({
    key: mention.key,
    id: mention.id?.open_id ?? mention.id?.union_id ?? mention.id?.user_id,
    name: mention.name,
    tenantKey: mention.tenant_key,
  }));
}

export class FeishuAdapter {
  constructor(logger) {
    this.logger = logger;
    this.id = "feishu";
    this.meta = { label: "Feishu / Lark", aliases: ["lark"] };
    this.capabilities = {
      inbound: { webhook: true, encryptedWebhook: true, text: true, group: true, direct: true },
      outbound: { text: true, replyTo: true },
    };
    this.tokenCache = new Map();
  }

  verifyToken(payload, accountConfig) {
    if (!accountConfig.verificationToken) {
      throw new GatewayError("FEISHU_VERIFICATION_TOKEN_REQUIRED", "Feishu verificationToken is not configured", { status: 503 });
    }
    const supplied = payload?.header?.token ?? payload?.token;
    if (!constantTimeEqual(accountConfig.verificationToken, supplied)) {
      throw new GatewayError("FEISHU_TOKEN_INVALID", "Feishu verification token does not match", { status: 401 });
    }
  }

  async receive({ accountId, accountConfig, headers, rawBody, body }) {
    const encrypted = typeof body?.encrypt === "string";
    const signaturePresent = Boolean(headers["x-lark-signature"]);
    let payload = body;

    if (accountConfig.encryptKey && signaturePresent) {
      const valid = verifyFeishuSignature({
        timestamp: headers["x-lark-request-timestamp"],
        nonce: headers["x-lark-request-nonce"],
        encryptKey: accountConfig.encryptKey,
        rawBody,
        signature: headers["x-lark-signature"],
        maxSkewSeconds: accountConfig.maxSkewSeconds ?? 300,
      });
      if (!valid) {
        throw new GatewayError("FEISHU_SIGNATURE_INVALID", "Feishu callback signature is invalid or expired", { status: 401 });
      }
    }

    if (encrypted) {
      payload = decryptFeishuPayload(accountConfig.encryptKey, body.encrypt);
    }

    const isChallenge = payload?.type === "url_verification" && typeof payload?.challenge === "string";
    if (accountConfig.encryptKey && !signaturePresent && !isChallenge) {
      throw new GatewayError("FEISHU_SIGNATURE_REQUIRED", "Signed Feishu callback headers are required for ordinary events", { status: 401 });
    }
    this.verifyToken(payload, accountConfig);

    if (isChallenge) {
      return {
        response: { status: 200, body: { challenge: payload.challenge } },
        events: [],
      };
    }

    const eventType = payload?.header?.event_type ?? payload?.type;
    if (eventType !== "im.message.receive_v1") {
      return { events: [], ignored: true, ignoredReason: `Unsupported Feishu event type: ${eventType ?? "unknown"}` };
    }

    const nativeEvent = payload.event ?? {};
    const nativeMessage = nativeEvent.message ?? {};
    const senderId = nativeEvent.sender?.sender_id ?? {};
    const sender = senderId.open_id ?? senderId.union_id ?? senderId.user_id;
    const chatId = nativeMessage.chat_id;
    if (!sender || !chatId) {
      throw new GatewayError("FEISHU_EVENT_INVALID", "Feishu message event is missing sender or chat id", { status: 400 });
    }
    const normalized = normalizeTrustedInbound({
      channel: this.id,
      accountId,
      platformEventId: payload.header?.event_id,
      kind: "message",
      occurredAt: parseFeishuTime(payload.header?.create_time),
      sender: {
        id: sender,
        type: nativeEvent.sender?.sender_type ?? "user",
        name: nativeEvent.sender?.sender_name,
      },
      conversation: {
        id: chatId,
        type: nativeMessage.chat_type === "p2p" ? "direct" : "group",
        threadId: nativeMessage.root_id || undefined,
        parentId: nativeMessage.parent_id || undefined,
      },
      message: {
        id: nativeMessage.message_id,
        type: nativeMessage.message_type ?? "unknown",
        text: parseTextContent(nativeMessage),
        replyToId: nativeMessage.parent_id || undefined,
        mentions: normalizeMentions(nativeMessage.mentions),
        media: [],
      },
      raw: redactFeishuPayload(payload),
      metadata: {
        tenantKey: nativeEvent.sender?.tenant_key,
        appId: payload.header?.app_id,
      },
    });
    return { events: [normalized] };
  }

  tokenCacheKey(accountId, accountConfig) {
    return `${accountId}:${accountConfig.domain ?? "feishu"}:${accountConfig.appId}`;
  }

  async tenantToken(accountId, accountConfig) {
    const cacheKey = this.tokenCacheKey(accountId, accountConfig);
    const cached = this.tokenCache.get(cacheKey);
    if (cached && cached.expiresAt > Date.now() + 60_000) {
      return cached.token;
    }
    if (!accountConfig.appId || !accountConfig.appSecret) {
      throw new PlatformSendError("FEISHU_CREDENTIALS_REQUIRED", "Feishu appId and appSecret are required for outbound messages", {
        status: 503,
        safeToRetry: true,
      });
    }
    let result;
    try {
      result = await fetchJson(`${domainBase(accountConfig.domain)}/open-apis/auth/v3/tenant_access_token/internal`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: { app_id: accountConfig.appId, app_secret: accountConfig.appSecret },
        timeoutMs: accountConfig.timeoutMs ?? 15_000,
        operation: "Feishu tenant token request",
        classifyHttpError(status) {
          return { safeToRetry: true, ambiguous: false, retryable: status >= 500 };
        },
      });
    } catch (error) {
      if (error instanceof PlatformSendError) {
        throw new PlatformSendError(error.code, error.message, {
          status: error.status,
          details: error.details,
          retryable: true,
          ambiguous: false,
          safeToRetry: true,
          cause: error,
        });
      }
      throw error;
    }
    const data = result.data ?? {};
    if (data.code !== 0 || !data.tenant_access_token) {
      this.tokenCache.delete(cacheKey);
      throw new PlatformSendError("FEISHU_TOKEN_FAILED", "Feishu did not return a tenant_access_token", {
        details: data,
        safeToRetry: true,
      });
    }
    const expiresIn = Number(data.expire ?? data.expires_in ?? 7200);
    this.tokenCache.set(cacheKey, {
      token: data.tenant_access_token,
      expiresAt: Date.now() + Math.max(60, expiresIn) * 1000,
    });
    return data.tenant_access_token;
  }

  async send({ request, accountConfig }) {
    const token = await this.tenantToken(request.accountId, accountConfig);
    const base = domainBase(accountConfig.domain);
    const content = JSON.stringify({ text: request.text });
    let url;
    let body;
    if (request.replyToId) {
      url = `${base}/open-apis/im/v1/messages/${encodeURIComponent(request.replyToId)}/reply`;
      body = {
        msg_type: "text",
        content,
        ...(request.threadId ? { reply_in_thread: true } : {}),
      };
    } else {
      const receiveIdType = request.to.receiveIdType ?? "chat_id";
      url = `${base}/open-apis/im/v1/messages?receive_id_type=${encodeURIComponent(receiveIdType)}`;
      body = {
        receive_id: request.to.conversationId,
        msg_type: "text",
        content,
      };
    }
    const result = await fetchJson(url, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${token}`,
      },
      body,
      timeoutMs: accountConfig.timeoutMs ?? 15_000,
      maxResponseBytes: accountConfig.maxResponseBytes ?? 1_048_576,
      operation: "Feishu send message",
      classifyHttpError(status) {
        return {
          safeToRetry: status >= 400 && status < 500,
          ambiguous: status >= 500,
          retryable: status >= 500,
        };
      },
    });
    const data = result.data ?? {};
    if (data.code !== 0) {
      this.tokenCache.delete(this.tokenCacheKey(request.accountId, accountConfig));
      throw new PlatformSendError("FEISHU_SEND_REJECTED", data.msg || "Feishu rejected the message", {
        details: data,
        safeToRetry: true,
      });
    }
    const messageId = data.data?.message_id ?? data.data?.message?.message_id;
    if (!messageId) {
      throw new PlatformSendError("FEISHU_SEND_AMBIGUOUS", "Feishu returned success without a message_id", {
        details: data,
        ambiguous: true,
        safeToRetry: false,
      });
    }
    return {
      platformMessageId: messageId,
      conversationId: request.to.conversationId,
      raw: data,
    };
  }
}
