import http from "node:http";
import { GatewayError, PlatformSendError } from "../core/errors.js";
import { decryptFeishuPayload, constantTimeEqual, verifyFeishuSignature } from "../util/crypto.js";
import { fetchJson } from "../util/http.js";
import { normalizeTrustedInbound } from "../core/validation.js";

// 2026-08-17：飞书 Card 按钮回调（card.action.trigger）业务端地址。
// Gateway 收到 Card 事件后同步代理到这里，业务端把响应原样回给飞书（飞书要求 2s 内）。
const CARD_CALLBACK_BUSINESS_HOST = "127.0.0.1";
const CARD_CALLBACK_BUSINESS_PORT = 8080;
const CARD_CALLBACK_BUSINESS_PATH = "/api/feishu/card-callback";
const CARD_CALLBACK_PROXY_TIMEOUT_MS = 5_000;

function proxyCardActionToBusiness(payload, logger) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify(payload);
    const req = http.request(
      {
        host: CARD_CALLBACK_BUSINESS_HOST,
        port: CARD_CALLBACK_BUSINESS_PORT,
        method: "POST",
        path: CARD_CALLBACK_BUSINESS_PATH,
        headers: {
          "content-type": "application/json",
          "content-length": Buffer.byteLength(body, "utf8"),
        },
        timeout: CARD_CALLBACK_PROXY_TIMEOUT_MS,
      },
      (res) => {
        let chunks = "";
        res.setEncoding("utf8");
        res.on("data", (chunk) => { chunks += chunk; });
        res.on("end", () => {
          try {
            resolve(JSON.parse(chunks));
          } catch (err) {
            reject(new Error(`business returned non-JSON (status=${res.statusCode}): ${chunks.slice(0, 200)}`));
          }
        });
      },
    );
    req.on("error", reject);
    req.on("timeout", () => {
      req.destroy(new Error("business proxy timeout"));
    });
    req.write(body);
    req.end();
  });
}

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

    // 2026-08-17：url_verification 必须在 verifyToken 之前返回。
    // 原因：飞书的 url_verification payload 是 {"challenge": "...", "type": "url_verification"}，
    // 没有 token。如果先跑 verifyToken，constantTimeEqual(token, undefined) 永远 false → 401。
    // 飞书收到 401 → 标记"目标回调服务器未在线"。bug fix。
    if (isChallenge) {
      return {
        response: { status: 200, body: { challenge: payload.challenge } },
        events: [],
      };
    }

    if (accountConfig.encryptKey && !signaturePresent) {
      throw new GatewayError("FEISHU_SIGNATURE_REQUIRED", "Signed Feishu callback headers are required for ordinary events", { status: 401 });
    }
    this.verifyToken(payload, accountConfig);

    const eventType = payload?.header?.event_type ?? payload?.type;

    // 2026-08-17：飞书 Card 按钮回调（card.action.trigger）需要同步响应给飞书（2s 内）。
    // 这里把解密后的 payload 同步代理到业务端 web/server.py:8080/api/feishu/card-callback，
    // 业务端返回 {"status":"ok"} / {"toast":{...}} / {"card":{...}}，原样回给飞书。
    // Card 事件不入 Gateway 事件流（不需要异步分发）。
    if (eventType === "card.action.trigger") {
      try {
        const businessReply = await proxyCardActionToBusiness(payload, this.logger);
        return {
          response: { status: 200, body: businessReply },
          events: [],
        };
      } catch (err) {
        this.logger.error("card action proxy failed", {
          error: err.message,
          open_message_id: payload?.event?.context?.open_message_id,
        });
        // 业务端挂了也要给飞书回 200（不能 4xx/5xx，否则飞书会重试轰炸）
        // 回一个 toast 提示，前端用户能看到。
        return {
          response: {
            status: 200,
            body: { toast: { type: "error", content: "服务暂时不可用，请稍后重试" } },
          },
          events: [],
        };
      }
    }

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
    // 2026-08-17：支持飞书交互式卡片（msgType="interactive"）。
    // - 当 request.msgType === "interactive" 且 request.content 非空时：
    //     msg_type = "interactive"，content = 客户端透传的 Card JSON 字符串
    // - 否则走原文本路径：content = JSON.stringify({ text: request.text })
    const msgType = request.msgType === "interactive" && typeof request.content === "string" && request.content
      ? "interactive"
      : "text";
    const content = msgType === "interactive"
      ? request.content
      : JSON.stringify({ text: request.text });
    let url;
    let body;
    if (request.replyToId) {
      url = `${base}/open-apis/im/v1/messages/${encodeURIComponent(request.replyToId)}/reply`;
      body = {
        msg_type: msgType,
        content,
        ...(request.threadId ? { reply_in_thread: true } : {}),
      };
    } else {
      const receiveIdType = request.to.receiveIdType ?? "chat_id";
      url = `${base}/open-apis/im/v1/messages?receive_id_type=${encodeURIComponent(receiveIdType)}`;
      body = {
        receive_id: request.to.conversationId,
        msg_type: msgType,
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
