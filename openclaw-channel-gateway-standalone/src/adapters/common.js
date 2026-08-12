import { normalizeTrustedInbound, requireObject } from "../core/validation.js";

export function normalizeGenericWebhookBody(body, { channel, accountId }) {
  requireObject(body, "webhook body");
  const messageInput = body.message && typeof body.message === "object"
    ? body.message
    : {
        id: body.message_id,
        type: body.message_type ?? "text",
        text: body.text ?? "",
        reply_to_id: body.reply_to_id,
        mentions: body.mentions,
        media: body.media,
      };
  const senderInput = body.sender && typeof body.sender === "object"
    ? body.sender
    : { id: body.sender_id, name: body.sender_name, type: body.sender_type ?? "user" };
  const conversationInput = body.conversation && typeof body.conversation === "object"
    ? body.conversation
    : {
        id: body.conversation_id ?? body.chat_id,
        type: body.conversation_type ?? body.chat_type ?? "direct",
        thread_id: body.thread_id,
        parent_id: body.parent_id,
      };
  return normalizeTrustedInbound({
    channel,
    accountId,
    platformEventId: body.platform_event_id ?? body.event_id ?? body.id,
    kind: body.kind ?? "message",
    occurredAt: body.occurred_at ?? body.timestamp ?? new Date().toISOString(),
    sender: senderInput,
    conversation: conversationInput,
    message: messageInput,
    raw: body,
    metadata: body.metadata,
  });
}
