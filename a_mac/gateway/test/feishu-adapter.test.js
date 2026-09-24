import test from "node:test";
import assert from "node:assert/strict";
import { createCipheriv, createHash } from "node:crypto";
import { FeishuAdapter } from "../src/adapters/feishu.js";
import { sha256Hex } from "../src/util/crypto.js";

const logger = { info() {}, warn() {}, error() {}, debug() {} };

function encrypt(encryptKey, payload) {
  const key = createHash("sha256").update(encryptKey, "utf8").digest();
  const iv = Buffer.alloc(16, 3);
  const cipher = createCipheriv("aes-256-cbc", key, iv);
  const ciphertext = Buffer.concat([cipher.update(JSON.stringify(payload), "utf8"), cipher.final()]);
  return Buffer.concat([iv, ciphertext]).toString("base64");
}

test("Feishu adapter accepts an encrypted URL verification challenge without signature after token verification", async () => {
  const adapter = new FeishuAdapter(logger);
  const accountConfig = { verificationToken: "verify", encryptKey: "encrypt-key" };
  const body = { encrypt: encrypt(accountConfig.encryptKey, { type: "url_verification", token: "verify", challenge: "abc" }) };
  const result = await adapter.receive({
    accountId: "default",
    accountConfig,
    headers: {},
    rawBody: Buffer.from(JSON.stringify(body)),
    body,
  });
  assert.deepEqual(result.response.body, { challenge: "abc" });
});

test("Feishu adapter normalizes signed message events", async () => {
  const adapter = new FeishuAdapter(logger);
  const accountConfig = { verificationToken: "verify", encryptKey: "encrypt-key", maxSkewSeconds: 300 };
  const payload = {
    schema: "2.0",
    header: {
      token: "verify",
      event_id: "event-1",
      event_type: "im.message.receive_v1",
      create_time: String(Date.now()),
      app_id: "app-1"
    },
    event: {
      sender: { sender_id: { open_id: "ou-user" }, sender_type: "user", tenant_key: "tenant" },
      message: {
        message_id: "om-message",
        chat_id: "oc-chat",
        chat_type: "p2p",
        message_type: "text",
        content: JSON.stringify({ text: "hello" })
      }
    }
  };
  const body = { encrypt: encrypt(accountConfig.encryptKey, payload) };
  const rawBody = Buffer.from(JSON.stringify(body));
  const timestamp = String(Math.floor(Date.now() / 1000));
  const nonce = "nonce";
  const signature = sha256Hex(Buffer.from(`${timestamp}${nonce}${accountConfig.encryptKey}`), rawBody);
  const result = await adapter.receive({
    accountId: "default",
    accountConfig,
    headers: {
      "x-lark-request-timestamp": timestamp,
      "x-lark-request-nonce": nonce,
      "x-lark-signature": signature,
    },
    rawBody,
    body,
  });
  assert.equal(result.events.length, 1);
  assert.equal(result.events[0].sender.id, "ou-user");
  assert.equal(result.events[0].conversation.id, "oc-chat");
  assert.equal(result.events[0].message.text, "hello");
  assert.equal(result.events[0].raw.header.token, "***redacted***");
});
