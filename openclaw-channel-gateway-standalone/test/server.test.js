import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { loadConfig } from "../src/config.js";
import { createApplication } from "../src/app.js";

const logger = { info() {}, warn() {}, error() {}, debug() {} };

async function jsonFetch(url, options = {}) {
  const response = await fetch(url, options);
  const body = await response.json();
  return { response, body };
}

test("HTTP API ingests, lists, and replies to a loopback event", async (t) => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "cg-server-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const configPath = path.join(directory, "config.json");
  await writeFile(configPath, JSON.stringify({
    server: { host: "127.0.0.1", port: 8787, apiKey: "test-api-key-123456789" },
    storage: { directory: "./data" },
    delivery: { callbackUrl: null },
    channels: {
      loopback: { enabled: true, accounts: { default: { webhookToken: "webhook-token" } } },
      generic: { enabled: false, accounts: {} },
      feishu: { enabled: false, accounts: {} }
    }
  }));
  const config = await loadConfig({ configPath });
  config.server.port = 0;
  const app = await createApplication({ config, logger });
  const address = await app.start();
  t.after(() => app.stop());
  const base = `http://127.0.0.1:${address.port}`;

  const health = await jsonFetch(`${base}/healthz`);
  assert.equal(health.response.status, 200);

  const inbound = await jsonFetch(`${base}/webhooks/loopback/default`, {
    method: "POST",
    headers: { "content-type": "application/json", "x-cg-webhook-token": "webhook-token" },
    body: JSON.stringify({
      event_id: "native-1",
      sender: { id: "user-1" },
      conversation: { id: "chat-1", type: "direct" },
      message: { id: "msg-1", type: "text", text: "hello" }
    })
  });
  assert.equal(inbound.response.status, 200);
  assert.equal(inbound.body.accepted, true);
  const eventId = inbound.body.event_ids[0];

  const listed = await jsonFetch(`${base}/v1/events`, {
    headers: { authorization: "Bearer test-api-key-123456789" }
  });
  assert.equal(listed.response.status, 200);
  assert.equal(listed.body.events.length, 1);
  assert.equal(listed.body.events[0].message.text, "hello");

  const reply = await jsonFetch(`${base}/v1/messages/reply`, {
    method: "POST",
    headers: {
      authorization: "Bearer test-api-key-123456789",
      "content-type": "application/json",
      "idempotency-key": "reply-1"
    },
    body: JSON.stringify({ eventId, text: "world", metadata: { alpha: 1, beta: 2 } })
  });
  assert.equal(reply.response.status, 201);
  assert.equal(reply.body.receipt.status, "sent");

  const replay = await jsonFetch(`${base}/v1/messages/reply`, {
    method: "POST",
    headers: {
      authorization: "Bearer test-api-key-123456789",
      "content-type": "application/json",
      "idempotency-key": "reply-1"
    },
    body: JSON.stringify({ eventId, text: "world", metadata: { beta: 2, alpha: 1 } })
  });
  assert.equal(replay.response.status, 200);
  assert.equal(replay.body.replayed, true);
});
