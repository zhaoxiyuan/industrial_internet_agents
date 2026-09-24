import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { loadConfig } from "../src/config.js";
import { createApplication } from "../src/app.js";

const logger = { info() {}, warn() {}, error() {}, debug() {} };

async function waitFor(predicate, timeoutMs = 3000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const value = await predicate();
    if (value) return value;
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  throw new Error("Timed out waiting for condition");
}

test("agent callback can acknowledge an event and return an outbound reply", async (t) => {
  const agentServer = http.createServer((request, response) => {
    let raw = "";
    request.setEncoding("utf8");
    request.on("data", (chunk) => { raw += chunk; });
    request.on("end", () => {
      const payload = JSON.parse(raw);
      response.setHeader("content-type", "application/json");
      response.end(JSON.stringify({
        ack: true,
        messages: [{ text: `echo:${payload.event.message.text}` }]
      }));
    });
  });
  await new Promise((resolve) => agentServer.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => agentServer.close(resolve)));
  const agentAddress = agentServer.address();

  const directory = await mkdtemp(path.join(os.tmpdir(), "cg-worker-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const configPath = path.join(directory, "config.json");
  await writeFile(configPath, JSON.stringify({
    server: { host: "127.0.0.1", port: 8787, apiKey: "test-api-key-123456789" },
    storage: { directory: "./data" },
    delivery: {
      callbackUrl: `http://127.0.0.1:${agentAddress.port}/events`,
      pollIntervalMs: 50,
      baseDelayMs: 10,
      maxDelayMs: 100,
      timeoutMs: 1000
    },
    channels: {
      loopback: { enabled: true, accounts: { default: { webhookToken: "token" } } },
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

  const inbound = await fetch(`${base}/webhooks/loopback/default`, {
    method: "POST",
    headers: { "content-type": "application/json", "x-cg-webhook-token": "token" },
    body: JSON.stringify({
      event_id: "worker-event",
      sender: { id: "u" },
      conversation: { id: "c" },
      message: { id: "m", text: "hello" }
    })
  });
  assert.equal(inbound.status, 200);

  const event = await waitFor(async () => {
    const response = await fetch(`${base}/v1/events`, { headers: { authorization: "Bearer test-api-key-123456789" } });
    const body = await response.json();
    return body.events[0]?.delivery?.status === "acked" ? body.events[0] : null;
  });
  assert.equal(event.delivery.status, "acked");

  const receiptsResponse = await fetch(`${base}/v1/receipts`, { headers: { authorization: "Bearer test-api-key-123456789" } });
  const receipts = await receiptsResponse.json();
  assert.equal(receipts.receipts.length, 1);
  assert.equal(receipts.receipts[0].status, "sent");
});
