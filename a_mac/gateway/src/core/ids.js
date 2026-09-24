import { createHash, randomUUID } from "node:crypto";

export function newId(prefix) {
  return `${prefix}_${randomUUID()}`;
}

export function stableSessionKey({ channel, accountId, conversationId, threadId }) {
  const raw = [channel, accountId, conversationId, threadId ?? ""].join("\u001f");
  const digest = createHash("sha256").update(raw).digest("base64url").slice(0, 24);
  return `cg:v1:${channel}:${accountId}:${digest}`;
}

export function inboundDedupeKey({ channel, accountId, platformEventId }) {
  if (!platformEventId) return null;
  return `${channel}\u001f${accountId}\u001f${platformEventId}`;
}
