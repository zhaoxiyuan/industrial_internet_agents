import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { GenericWebhookAdapter } from "../src/adapters/generic-webhook.js";
import { hmacSha256Hex } from "../src/util/crypto.js";

const logger = { info() {}, warn() {}, error() {}, debug() {} };

test("generic adapter verifies HMAC inbound and sends an idempotent delivery envelope", async (t) => {
  const received = [];
  const platform = http.createServer((request, response) => {
    const chunks = [];
    request.on("data", (chunk) => chunks.push(chunk));
    request.on("end", () => {
      received.push({
        authorization: request.headers.authorization,
        deliveryId: request.headers["x-cg-delivery-id"],
        body: JSON.parse(Buffer.concat(chunks).toString("utf8")),
      });
      response.writeHead(200, { "content-type": "application/json" });
      response.end(JSON.stringify({ message_id: "platform-message-1", conversation_id: "chat-1" }));
    });
  });
  await new Promise((resolve) => platform.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => platform.close(resolve)));
  const address = platform.address();

  const adapter = new GenericWebhookAdapter(logger);
  const body = {
    platform_event_id: "generic-event-1",
    sender_id: "user-1",
    conversation_id: "chat-1",
    message_id: "message-1",
    text: "hello",
  };
  const rawBody = Buffer.from(JSON.stringify(body));
  const timestamp = String(Math.floor(Date.now() / 1000));
  const secret = "generic-secret";
  const signature = hmacSha256Hex(secret, Buffer.concat([Buffer.from(`${timestamp}.`), rawBody]));

  const inbound = await adapter.receive({
    accountId: "default",
    accountConfig: { signatureRequired: true, webhookSecret: secret },
    headers: {
      "x-cg-timestamp": timestamp,
      "x-cg-signature": `sha256=${signature}`,
    },
    rawBody,
    body,
  });
  assert.equal(inbound.events[0].message.text, "hello");
  assert.equal(inbound.events[0].platformEventId, "generic-event-1");

  const outbound = await adapter.send({
    intentId: "out-1",
    accountConfig: {
      outboundUrl: `http://127.0.0.1:${address.port}/platform/send`,
      outboundBearerToken: "outbound-token",
    },
    request: {
      channel: "generic",
      accountId: "default",
      to: { conversationId: "chat-1" },
      text: "world",
      replyToId: "message-1",
      threadId: undefined,
      metadata: {},
    },
  });

  assert.equal(outbound.platformMessageId, "platform-message-1");
  assert.equal(received.length, 1);
  assert.equal(received[0].authorization, "Bearer outbound-token");
  assert.equal(received[0].deliveryId, "out-1");
  assert.equal(received[0].body.message.text, "world");
});
