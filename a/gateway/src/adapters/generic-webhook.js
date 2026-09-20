import { GatewayError, PlatformSendError } from "../core/errors.js";
import { newId } from "../core/ids.js";
import { constantTimeEqual, verifyGenericWebhookSignature } from "../util/crypto.js";
import { fetchJson } from "../util/http.js";
import { normalizeGenericWebhookBody } from "./common.js";

export class GenericWebhookAdapter {
  constructor(logger) {
    this.logger = logger;
    this.id = "generic";
    this.meta = { label: "Generic HTTP Webhook", aliases: ["webhook", "http"] };
    this.capabilities = {
      inbound: { webhook: true, hmac: true, text: true, mediaMetadata: true },
      outbound: { text: true, replyTo: true, thread: true },
    };
  }

  async receive({ accountId, accountConfig, headers, rawBody, body }) {
    const signatureRequired = accountConfig.signatureRequired ?? Boolean(accountConfig.webhookSecret);
    if (signatureRequired) {
      if (!accountConfig.webhookSecret) {
        throw new GatewayError("WEBHOOK_AUTH_NOT_CONFIGURED", "Generic webhook signature mode requires webhookSecret", { status: 503 });
      }
      const valid = verifyGenericWebhookSignature({
        secret: accountConfig.webhookSecret,
        timestamp: headers["x-cg-timestamp"],
        signature: headers["x-cg-signature"],
        rawBody,
        maxSkewSeconds: accountConfig.maxSkewSeconds ?? 300,
      });
      if (!valid) {
        throw new GatewayError("WEBHOOK_SIGNATURE_INVALID", "Generic webhook signature is invalid or expired", { status: 401 });
      }
    } else if (accountConfig.webhookToken) {
      const supplied = headers["x-cg-webhook-token"] ?? headers.authorization?.replace(/^Bearer\s+/i, "");
      if (!constantTimeEqual(accountConfig.webhookToken, supplied)) {
        throw new GatewayError("WEBHOOK_UNAUTHORIZED", "Invalid generic webhook token", { status: 401 });
      }
    } else {
      throw new GatewayError("WEBHOOK_AUTH_NOT_CONFIGURED", "Generic webhook account has no signature secret or token", { status: 503 });
    }
    return { events: [normalizeGenericWebhookBody(body, { channel: this.id, accountId })] };
  }

  async send({ request, accountConfig, intentId }) {
    if (!accountConfig.outboundUrl) {
      throw new PlatformSendError("OUTBOUND_NOT_CONFIGURED", "generic.outboundUrl is not configured for this account", {
        status: 501,
        safeToRetry: true,
      });
    }
    const headers = {
      "content-type": "application/json",
      "user-agent": "openclaw-channel-gateway-standalone/0.1",
      "x-cg-delivery-id": intentId,
      ...(accountConfig.outboundHeaders ?? {}),
    };
    if (accountConfig.outboundBearerToken) {
      headers.authorization = `Bearer ${accountConfig.outboundBearerToken}`;
    }
    const payload = {
      version: "1.0",
      delivery_id: intentId,
      channel: request.channel,
      account_id: request.accountId,
      to: {
        conversation_id: request.to.conversationId,
        receive_id_type: request.to.receiveIdType,
      },
      message: {
        type: "text",
        text: request.text,
        reply_to_id: request.replyToId,
        thread_id: request.threadId,
      },
      metadata: request.metadata,
    };
    const result = await fetchJson(accountConfig.outboundUrl, {
      method: "POST",
      headers,
      body: payload,
      timeoutMs: accountConfig.timeoutMs ?? 15_000,
      maxResponseBytes: accountConfig.maxResponseBytes ?? 1_048_576,
      operation: "generic outbound webhook",
      classifyHttpError(status) {
        return {
          safeToRetry: status >= 400 && status < 500,
          ambiguous: status >= 500,
          retryable: status >= 500,
        };
      },
    });
    const data = result.data ?? {};
    return {
      platformMessageId: data.message_id ?? data.messageId ?? data.id ?? newId("generic"),
      conversationId: data.conversation_id ?? data.conversationId ?? request.to.conversationId,
      raw: data,
    };
  }
}
