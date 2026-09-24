import { GatewayError } from "../core/errors.js";
import { newId } from "../core/ids.js";
import { constantTimeEqual } from "../util/crypto.js";
import { normalizeGenericWebhookBody } from "./common.js";

export class LoopbackAdapter {
  constructor(logger) {
    this.logger = logger;
    this.id = "loopback";
    this.meta = { label: "Loopback", aliases: ["local", "test"] };
    this.capabilities = {
      inbound: { webhook: true, text: true, mediaMetadata: true },
      outbound: { text: true, replyTo: true, localReceipt: true },
    };
  }

  async receive({ accountId, accountConfig, headers, body }) {
    const expected = accountConfig.webhookToken;
    if (expected) {
      const supplied = headers["x-cg-webhook-token"] ?? headers.authorization?.replace(/^Bearer\s+/i, "");
      if (!constantTimeEqual(expected, supplied)) {
        throw new GatewayError("WEBHOOK_UNAUTHORIZED", "Invalid loopback webhook token", { status: 401 });
      }
    }
    return { events: [normalizeGenericWebhookBody(body, { channel: this.id, accountId })] };
  }

  async send({ request, intentId }) {
    const platformMessageId = newId("loop");
    this.logger.info("loopback outbound message", {
      intent_id: intentId,
      platform_message_id: platformMessageId,
      account_id: request.accountId,
      conversation_id: request.to.conversationId,
      text_length: request.text.length,
    });
    return {
      platformMessageId,
      conversationId: request.to.conversationId,
      raw: { local: true },
    };
  }
}
