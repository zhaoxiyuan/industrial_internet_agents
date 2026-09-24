import test from "node:test";
import assert from "node:assert/strict";
import { createCipheriv, createHash, randomBytes } from "node:crypto";
import {
  decryptFeishuPayload,
  hmacSha256Hex,
  sha256Hex,
  verifyFeishuSignature,
  verifyGenericWebhookSignature,
} from "../src/util/crypto.js";

function encryptFeishu(encryptKey, value, iv = randomBytes(16)) {
  const key = createHash("sha256").update(encryptKey, "utf8").digest();
  const cipher = createCipheriv("aes-256-cbc", key, iv);
  const ciphertext = Buffer.concat([cipher.update(Buffer.from(JSON.stringify(value), "utf8")), cipher.final()]);
  return Buffer.concat([iv, ciphertext]).toString("base64");
}

test("generic webhook signature validates raw body and timestamp", () => {
  const secret = "generic-secret";
  const timestamp = String(Math.floor(Date.now() / 1000));
  const rawBody = Buffer.from('{"text":"hello"}');
  const signature = hmacSha256Hex(secret, Buffer.concat([Buffer.from(`${timestamp}.`), rawBody]));
  assert.equal(verifyGenericWebhookSignature({ secret, timestamp, rawBody, signature: `sha256=${signature}` }), true);
  assert.equal(verifyGenericWebhookSignature({ secret, timestamp, rawBody: Buffer.from('{"text":"changed"}'), signature }), false);
});

test("Feishu signature follows sha256(timestamp + nonce + encryptKey + raw body)", () => {
  const timestamp = String(Math.floor(Date.now() / 1000));
  const nonce = "n-1";
  const encryptKey = "ek-secret";
  const rawBody = Buffer.from('{"encrypt":"payload"}');
  const signature = sha256Hex(Buffer.from(`${timestamp}${nonce}${encryptKey}`), rawBody);
  assert.equal(verifyFeishuSignature({ timestamp, nonce, encryptKey, rawBody, signature }), true);
  assert.equal(verifyFeishuSignature({ timestamp, nonce, encryptKey, rawBody: Buffer.from("{}"), signature }), false);
});

test("Feishu encrypted callback decrypts to JSON", () => {
  const encryptKey = "a-strong-encrypt-key";
  const payload = { type: "url_verification", token: "token", challenge: "challenge" };
  const encrypted = encryptFeishu(encryptKey, payload, Buffer.alloc(16, 7));
  assert.deepEqual(decryptFeishuPayload(encryptKey, encrypted), payload);
});
