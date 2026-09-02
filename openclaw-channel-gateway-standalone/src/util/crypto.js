import {
  createDecipheriv,
  createHash,
  createHmac,
  timingSafeEqual,
} from "node:crypto";
import { GatewayError } from "../core/errors.js";

export function sha256Hex(...parts) {
  const hash = createHash("sha256");
  for (const part of parts) {
    hash.update(Buffer.isBuffer(part) ? part : Buffer.from(String(part), "utf8"));
  }
  return hash.digest("hex");
}

export function hmacSha256Hex(secret, data) {
  return createHmac("sha256", secret).update(data).digest("hex");
}

export function constantTimeEqual(left, right) {
  const a = Buffer.from(String(left ?? ""), "utf8");
  const b = Buffer.from(String(right ?? ""), "utf8");
  if (a.length !== b.length) {
    const padded = Buffer.alloc(Math.max(a.length, b.length));
    const other = Buffer.alloc(Math.max(a.length, b.length));
    a.copy(padded);
    b.copy(other);
    timingSafeEqual(padded, other);
    return false;
  }
  return timingSafeEqual(a, b);
}

export function verifyGenericWebhookSignature({ secret, timestamp, rawBody, signature, maxSkewSeconds = 300, now = Date.now() }) {
  if (!secret || !timestamp || !signature) {
    return false;
  }
  const parsedTimestamp = Number(timestamp);
  if (!Number.isFinite(parsedTimestamp)) {
    return false;
  }
  const timestampMs = parsedTimestamp < 10_000_000_000 ? parsedTimestamp * 1000 : parsedTimestamp;
  if (Math.abs(now - timestampMs) > maxSkewSeconds * 1000) {
    return false;
  }
  const expected = hmacSha256Hex(secret, Buffer.concat([
    Buffer.from(String(timestamp), "utf8"),
    Buffer.from(".", "utf8"),
    rawBody,
  ]));
  const supplied = String(signature).startsWith("sha256=")
    ? String(signature).slice("sha256=".length)
    : String(signature);
  return constantTimeEqual(expected, supplied.toLowerCase());
}

export function verifyFeishuSignature({ timestamp, nonce, encryptKey, rawBody, signature, maxSkewSeconds = 300, now = Date.now() }) {
  if (!timestamp || !nonce || !encryptKey || !signature) {
    return false;
  }
  const parsedTimestamp = Number(timestamp);
  if (!Number.isFinite(parsedTimestamp)) {
    return false;
  }
  const timestampMs = parsedTimestamp < 10_000_000_000 ? parsedTimestamp * 1000 : parsedTimestamp;
  if (Math.abs(now - timestampMs) > maxSkewSeconds * 1000) {
    return false;
  }
  const expected = sha256Hex(
    Buffer.from(`${timestamp}${nonce}${encryptKey}`, "utf8"),
    rawBody,
  );
  return constantTimeEqual(expected, String(signature).toLowerCase());
}

function decryptWithAutoPadding(key, iv, ciphertext, autoPadding) {
  const decipher = createDecipheriv("aes-256-cbc", key, iv);
  decipher.setAutoPadding(autoPadding);
  return Buffer.concat([decipher.update(ciphertext), decipher.final()]);
}

function stripPkcs7OrZeroPadding(buffer) {
  if (buffer.length === 0) {
    return buffer;
  }
  const pad = buffer[buffer.length - 1];
  if (pad >= 1 && pad <= 16 && pad <= buffer.length) {
    const padding = buffer.subarray(buffer.length - pad);
    if ([...padding].every((value) => value === pad)) {
      return buffer.subarray(0, buffer.length - pad);
    }
  }
  let end = buffer.length;
  while (end > 0 && buffer[end - 1] === 0) {
    end -= 1;
  }
  return buffer.subarray(0, end);
}

export function decryptFeishuPayload(encryptKey, base64Ciphertext) {
  if (!encryptKey) {
    throw new GatewayError("FEISHU_ENCRYPT_KEY_REQUIRED", "Feishu encryptKey is required for encrypted callbacks", { status: 401 });
  }
  let combined;
  try {
    combined = Buffer.from(base64Ciphertext, "base64");
  } catch (error) {
    throw new GatewayError("FEISHU_DECRYPT_FAILED", "Feishu encrypted payload is not valid Base64", { status: 400, cause: error });
  }
  if (combined.length <= 16 || (combined.length - 16) % 16 !== 0) {
    throw new GatewayError("FEISHU_DECRYPT_FAILED", "Feishu encrypted payload has an invalid length", { status: 400 });
  }
  const key = createHash("sha256").update(encryptKey, "utf8").digest();
  const iv = combined.subarray(0, 16);
  const ciphertext = combined.subarray(16);
  let plaintext;
  try {
    plaintext = decryptWithAutoPadding(key, iv, ciphertext, true);
  } catch {
    try {
      plaintext = stripPkcs7OrZeroPadding(decryptWithAutoPadding(key, iv, ciphertext, false));
    } catch (error) {
      throw new GatewayError("FEISHU_DECRYPT_FAILED", "Unable to decrypt Feishu callback", { status: 401, cause: error });
    }
  }
  try {
    return JSON.parse(plaintext.toString("utf8"));
  } catch (error) {
    throw new GatewayError("FEISHU_DECRYPT_FAILED", "Decrypted Feishu callback is not valid JSON", { status: 400, cause: error });
  }
}
