import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { JsonStateStore } from "../src/core/store.js";

const logger = { info() {}, warn() {}, error() {}, debug() {} };

async function makeStore() {
  const directory = await mkdtemp(path.join(os.tmpdir(), "cg-store-"));
  const store = new JsonStateStore({
    directory,
    path: path.join(directory, "state.json"),
    maxEvents: 100,
    maxReceipts: 100,
    dedupeRetentionMs: 60_000,
    acknowledgedRetentionMs: 60_000,
  }, logger);
  await store.init();
  return { store, directory };
}

test("state store persists inbound dedupe and outbound receipts", async (t) => {
  const { store, directory } = await makeStore();
  t.after(() => rm(directory, { recursive: true, force: true }));
  const envelope = {
    id: "evt-test",
    channel: "loopback",
    accountId: "default",
    platformEventId: "native-1",
    receivedAt: new Date().toISOString(),
    session: { key: "session-1" },
    sender: { id: "user-1" },
    conversation: { id: "chat-1" },
    message: { text: "hello" },
  };
  const first = await store.addInboundEvent(envelope, "dedupe-1");
  const second = await store.addInboundEvent({ ...envelope, id: "evt-other" }, "dedupe-1");
  assert.equal(first.duplicate, false);
  assert.equal(second.duplicate, true);
  assert.equal(second.record.id, first.record.id);

  const created = await store.createOutboundIntent({ text: "reply" }, "idem-1");
  await store.markOutboundSending(created.intent.id);
  const completed = await store.completeOutbound(created.intent.id, {
    status: "sent",
    platformMessageId: "platform-1",
    createdAt: new Date().toISOString(),
  });
  assert.equal(completed.intent.status, "sent");
  assert.equal(completed.receipt.platformMessageId, "platform-1");
});


test("outbound send preparation atomically allows only one concurrent sender", async (t) => {
  const { store, directory } = await makeStore();
  t.after(() => rm(directory, { recursive: true, force: true }));
  const created = await store.createOutboundIntent({ text: "reply" }, "idem-concurrent");

  const results = await Promise.allSettled([
    store.prepareOutboundSend(created.intent.id),
    store.prepareOutboundSend(created.intent.id),
  ]);
  const fulfilled = results.filter((result) => result.status === "fulfilled");
  const rejected = results.filter((result) => result.status === "rejected");

  assert.equal(fulfilled.length, 1);
  assert.equal(fulfilled[0].value.intent.status, "sending");
  assert.equal(rejected.length, 1);
  assert.equal(rejected[0].reason.code, "OUTBOUND_IN_PROGRESS");
});
