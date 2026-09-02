import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { JsonStateStore } from "../src/core/store.js";

const logger = { info() {}, warn() {}, error() {}, debug() {} };

function config(directory) {
  return {
    directory,
    path: path.join(directory, "state.json"),
    maxEvents: 100,
    maxReceipts: 100,
    dedupeRetentionMs: 60_000,
    acknowledgedRetentionMs: 60_000,
  };
}

test("restart recovers processing inbound events and protects in-flight outbound sends", async (t) => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "cg-recovery-"));
  t.after(() => rm(directory, { recursive: true, force: true }));

  const first = new JsonStateStore(config(directory), logger);
  await first.init();
  await first.addInboundEvent({
    id: "event-1",
    channel: "loopback",
    accountId: "default",
    platformEventId: "platform-1",
    receivedAt: new Date().toISOString(),
    session: { key: "session-1" },
    sender: { id: "user-1" },
    conversation: { id: "chat-1" },
    message: { text: "hello" },
  }, "dedupe-1");
  const claimed = await first.claimNextDueEvent(new Set());
  assert.equal(claimed.delivery.status, "processing");

  const created = await first.createOutboundIntent({ text: "reply" }, "idem-recovery");
  await first.markOutboundSending(created.intent.id);

  const second = new JsonStateStore(config(directory), logger);
  await second.init();
  const event = await second.getEvent("event-1");
  const intent = await second.getOutboundIntent(created.intent.id);

  assert.equal(event.delivery.status, "pending");
  assert.equal(event.delivery.lastError.code, "RECOVERED_AFTER_RESTART");
  assert.equal(intent.status, "unknown");
  assert.equal(intent.lastError.code, "UNKNOWN_AFTER_RESTART");
});
