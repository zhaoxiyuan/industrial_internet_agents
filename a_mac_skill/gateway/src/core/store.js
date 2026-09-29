import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";
import { GatewayError } from "./errors.js";
import { newId } from "./ids.js";

const STATE_VERSION = 1;

function initialState() {
  return {
    version: STATE_VERSION,
    nextSequence: 1,
    events: [],
    dedupe: {},
    outboundIntents: [],
    receipts: [],
  };
}

function clone(value) {
  return structuredClone(value);
}

export class JsonStateStore {
  constructor(config, logger) {
    this.config = config;
    this.logger = logger;
    this.state = initialState();
    this.ready = false;
    this.tail = Promise.resolve();
  }

  async init() {
    await mkdir(this.config.directory, { recursive: true, mode: 0o700 });
    try {
      const raw = await readFile(this.config.path, "utf8");
      const parsed = JSON.parse(raw);
      const dedupeValid = parsed.dedupe === undefined
        || (parsed.dedupe && typeof parsed.dedupe === "object" && !Array.isArray(parsed.dedupe));
      const receiptsValid = parsed.receipts === undefined || Array.isArray(parsed.receipts);
      const sequenceValid = parsed.nextSequence === undefined
        || (Number.isInteger(parsed.nextSequence) && parsed.nextSequence >= 1);
      if (
        parsed.version !== STATE_VERSION
        || !Array.isArray(parsed.events)
        || !Array.isArray(parsed.outboundIntents)
        || !dedupeValid
        || !receiptsValid
        || !sequenceValid
      ) {
        throw new Error("Unsupported state file schema");
      }
      this.state = { ...initialState(), ...parsed };
    } catch (error) {
      if (error?.code !== "ENOENT") {
        throw new GatewayError("STATE_LOAD_FAILED", `Unable to load state file: ${this.config.path}`, { status: 500, cause: error });
      }
      await this.persist();
    }

    let changed = false;
    const highestSequence = this.state.events.reduce(
      (maximum, event) => Math.max(maximum, Number.isInteger(event?.sequence) ? event.sequence : 0),
      0,
    );
    if (this.state.nextSequence <= highestSequence) {
      this.state.nextSequence = highestSequence + 1;
      changed = true;
    }
    const now = Date.now();
    for (const event of this.state.events) {
      if (event.delivery?.status === "processing") {
        event.delivery.status = "pending";
        event.delivery.nextAttemptAt = now;
        event.delivery.lastError = { code: "RECOVERED_AFTER_RESTART", message: "Event was processing when the gateway restarted" };
        changed = true;
      }
    }
    for (const intent of this.state.outboundIntents) {
      if (intent.status === "sending") {
        intent.status = "unknown";
        intent.updatedAt = new Date().toISOString();
        intent.lastError = {
          code: "UNKNOWN_AFTER_RESTART",
          message: "The process restarted while the platform send was in progress; automatic replay is disabled",
        };
        changed = true;
      }
    }
    changed = this.compact(now) || changed;
    if (changed) await this.persist();
    this.ready = true;
  }

  async enqueue(operation) {
    const run = this.tail.then(operation, operation);
    this.tail = run.catch(() => {});
    return await run;
  }

  async persist() {
    const temporary = `${this.config.path}.tmp-${process.pid}-${Date.now()}`;
    const payload = `${JSON.stringify(this.state, null, 2)}\n`;
    await writeFile(temporary, payload, { encoding: "utf8", mode: 0o600 });
    await rename(temporary, this.config.path);
  }

  compact(now = Date.now()) {
    let changed = false;
    for (const [key, entry] of Object.entries(this.state.dedupe)) {
      if (!entry || entry.expiresAt <= now) {
        delete this.state.dedupe[key];
        changed = true;
      }
    }
    const acknowledgedCutoff = now - this.config.acknowledgedRetentionMs;
    const before = this.state.events.length;
    this.state.events = this.state.events.filter((event) => {
      if (!["acked", "ignored"].includes(event.delivery?.status)) return true;
      const completedAt = Date.parse(event.delivery.completedAt ?? event.receivedAt);
      return !Number.isFinite(completedAt) || completedAt >= acknowledgedCutoff;
    });
    if (this.state.events.length !== before) changed = true;

    if (this.state.events.length > this.config.maxEvents) {
      const removable = this.state.events
        .filter((event) => ["acked", "ignored", "dead_letter"].includes(event.delivery?.status))
        .sort((a, b) => a.sequence - b.sequence);
      const removeCount = this.state.events.length - this.config.maxEvents;
      const ids = new Set(removable.slice(0, removeCount).map((event) => event.id));
      if (ids.size > 0) {
        this.state.events = this.state.events.filter((event) => !ids.has(event.id));
        changed = true;
      }
    }
    if (this.state.receipts.length > this.config.maxReceipts) {
      this.state.receipts = this.state.receipts.slice(-this.config.maxReceipts);
      changed = true;
    }
    if (this.state.outboundIntents.length > this.config.maxReceipts * 2) {
      const terminal = this.state.outboundIntents.filter((intent) => ["sent", "failed"].includes(intent.status));
      const removeCount = this.state.outboundIntents.length - this.config.maxReceipts * 2;
      const ids = new Set(terminal.slice(0, removeCount).map((intent) => intent.id));
      this.state.outboundIntents = this.state.outboundIntents.filter((intent) => !ids.has(intent.id));
      changed = changed || ids.size > 0;
    }
    return changed;
  }

  async addInboundEvent(envelope, dedupeKey) {
    return await this.enqueue(async () => {
      const now = Date.now();
      if (dedupeKey) {
        const duplicate = this.state.dedupe[dedupeKey];
        if (duplicate && duplicate.expiresAt > now) {
          const existing = this.state.events.find((event) => event.id === duplicate.eventId);
          if (existing) return { record: clone(existing), duplicate: true };
        }
      }
      const record = {
        ...clone(envelope),
        id: envelope.id ?? newId("evt"),
        sequence: this.state.nextSequence++,
        receivedAt: envelope.receivedAt ?? new Date(now).toISOString(),
        delivery: {
          status: "pending",
          attempts: 0,
          nextAttemptAt: now,
          claimedAt: null,
          completedAt: null,
          lastError: null,
        },
      };
      this.state.events.push(record);
      if (dedupeKey) {
        this.state.dedupe[dedupeKey] = {
          eventId: record.id,
          expiresAt: now + this.config.dedupeRetentionMs,
        };
      }
      this.compact(now);
      await this.persist();
      return { record: clone(record), duplicate: false };
    });
  }

  async claimNextDueEvent(activeSessionKeys = new Set(), now = Date.now()) {
    return await this.enqueue(async () => {
      const candidate = this.state.events
        .filter((event) => event.delivery?.status === "pending")
        .filter((event) => Number(event.delivery.nextAttemptAt ?? 0) <= now)
        .filter((event) => !activeSessionKeys.has(event.session?.key))
        .sort((a, b) => a.sequence - b.sequence)[0];
      if (!candidate) return null;
      candidate.delivery.status = "processing";
      candidate.delivery.attempts += 1;
      candidate.delivery.claimedAt = new Date(now).toISOString();
      await this.persist();
      return clone(candidate);
    });
  }

  async ackEvent(id, outcome = {}) {
    return await this.updateEvent(id, (event) => {
      event.delivery.status = outcome.status ?? "acked";
      event.delivery.completedAt = new Date().toISOString();
      event.delivery.lastError = null;
      event.delivery.outcome = outcome.details ?? null;
    });
  }

  async retryEvent(id, error, nextAttemptAt = Date.now()) {
    return await this.updateEvent(id, (event) => {
      event.delivery.status = "pending";
      event.delivery.nextAttemptAt = nextAttemptAt;
      event.delivery.lastError = error;
      event.delivery.claimedAt = null;
    });
  }

  async deadLetterEvent(id, error) {
    return await this.updateEvent(id, (event) => {
      event.delivery.status = "dead_letter";
      event.delivery.completedAt = new Date().toISOString();
      event.delivery.lastError = error;
    });
  }

  async updateEvent(id, updater) {
    return await this.enqueue(async () => {
      const event = this.state.events.find((item) => item.id === id);
      if (!event) throw new GatewayError("EVENT_NOT_FOUND", `Event not found: ${id}`, { status: 404 });
      updater(event);
      this.compact();
      await this.persist();
      return clone(event);
    });
  }

  async getEvent(id) {
    const event = this.state.events.find((item) => item.id === id);
    return event ? clone(event) : null;
  }

  async listEvents(options = {}) {
    const {
      afterSequence = 0,
      limit = 100,
      status,
      channel,
      sessionKey,
    } = options;
    return this.state.events
      .filter((event) => event.sequence > afterSequence)
      .filter((event) => !status || event.delivery?.status === status)
      .filter((event) => !channel || event.channel === channel)
      .filter((event) => !sessionKey || event.session?.key === sessionKey)
      .sort((a, b) => a.sequence - b.sequence)
      .slice(0, Math.max(1, Math.min(Number(limit) || 100, 1000)))
      .map(clone);
  }

  async createOutboundIntent(request, idempotencyKey) {
    return await this.enqueue(async () => {
      if (idempotencyKey) {
        const existing = this.state.outboundIntents.find((intent) => intent.idempotencyKey === idempotencyKey);
        if (existing) return { intent: clone(existing), existing: true };
      }
      const now = new Date().toISOString();
      const intent = {
        id: newId("out"),
        idempotencyKey: idempotencyKey ?? null,
        request: clone(request),
        status: "created",
        attempts: 0,
        createdAt: now,
        updatedAt: now,
        lastError: null,
        receiptId: null,
      };
      this.state.outboundIntents.push(intent);
      await this.persist();
      return { intent: clone(intent), existing: false };
    });
  }

  async prepareOutboundSend(id, { force = false } = {}) {
    return await this.enqueue(async () => {
      const intent = this.state.outboundIntents.find((item) => item.id === id);
      if (!intent) throw new GatewayError("OUTBOUND_INTENT_NOT_FOUND", `Outbound intent not found: ${id}`, { status: 404 });
      if (intent.status === "sent") {
        return { intent: clone(intent), replayed: true };
      }
      if (intent.status === "sending") {
        throw new GatewayError("OUTBOUND_IN_PROGRESS", "The outbound message is already being sent", {
          status: 409,
          details: { intent_id: intent.id },
        });
      }
      if (intent.status === "unknown" && !force) {
        throw new GatewayError("OUTBOUND_STATE_UNKNOWN", "The previous platform send has an unknown outcome; automatic replay is disabled", {
          status: 409,
          ambiguous: true,
          details: {
            intent_id: intent.id,
            recovery: "Inspect the platform and retry with force=true only when duplication is acceptable.",
          },
        });
      }
      if (!["created", "failed", "unknown"].includes(intent.status)) {
        throw new GatewayError("OUTBOUND_STATE_INVALID", `Outbound intent cannot be sent from state: ${intent.status}`, {
          status: 409,
          details: { intent_id: intent.id },
        });
      }
      intent.status = "sending";
      intent.attempts += 1;
      intent.updatedAt = new Date().toISOString();
      intent.lastError = null;
      await this.persist();
      return { intent: clone(intent), replayed: false };
    });
  }

  async markOutboundSending(id) {
    const prepared = await this.prepareOutboundSend(id, { force: true });
    if (prepared.replayed) {
      throw new GatewayError("OUTBOUND_ALREADY_SENT", "The outbound message has already been sent", {
        status: 409,
        details: { intent_id: id },
      });
    }
    return prepared.intent;
  }

  async completeOutbound(id, receipt) {
    return await this.enqueue(async () => {
      const intent = this.state.outboundIntents.find((item) => item.id === id);
      if (!intent) throw new GatewayError("OUTBOUND_INTENT_NOT_FOUND", `Outbound intent not found: ${id}`, { status: 404 });
      const storedReceipt = { id: receipt.id ?? newId("rcpt"), ...clone(receipt) };
      this.state.receipts.push(storedReceipt);
      intent.status = "sent";
      intent.receiptId = storedReceipt.id;
      intent.updatedAt = new Date().toISOString();
      intent.lastError = null;
      this.compact();
      await this.persist();
      return { intent: clone(intent), receipt: clone(storedReceipt) };
    });
  }

  async failOutbound(id, error, { unknown = false } = {}) {
    return await this.updateOutboundIntent(id, (intent) => {
      intent.status = unknown ? "unknown" : "failed";
      intent.updatedAt = new Date().toISOString();
      intent.lastError = clone(error);
    });
  }

  async updateOutboundIntent(id, updater) {
    return await this.enqueue(async () => {
      const intent = this.state.outboundIntents.find((item) => item.id === id);
      if (!intent) throw new GatewayError("OUTBOUND_INTENT_NOT_FOUND", `Outbound intent not found: ${id}`, { status: 404 });
      updater(intent);
      await this.persist();
      return clone(intent);
    });
  }

  async getOutboundIntent(id) {
    const intent = this.state.outboundIntents.find((item) => item.id === id);
    return intent ? clone(intent) : null;
  }

  async listOutboundIntents({ status, limit = 100 } = {}) {
    return this.state.outboundIntents
      .filter((intent) => !status || intent.status === status)
      .slice(-Math.max(1, Math.min(Number(limit) || 100, 1000)))
      .reverse()
      .map(clone);
  }

  async listReceipts({ limit = 100 } = {}) {
    return this.state.receipts
      .slice(-Math.max(1, Math.min(Number(limit) || 100, 1000)))
      .reverse()
      .map(clone);
  }

  async getReceipt(id) {
    const receipt = this.state.receipts.find((item) => item.id === id);
    return receipt ? clone(receipt) : null;
  }

  stats() {
    const eventCounts = {};
    for (const event of this.state.events) {
      const status = event.delivery?.status ?? "unknown";
      eventCounts[status] = (eventCounts[status] ?? 0) + 1;
    }
    const outboundCounts = {};
    for (const intent of this.state.outboundIntents) {
      outboundCounts[intent.status] = (outboundCounts[intent.status] ?? 0) + 1;
    }
    return {
      ready: this.ready,
      events: eventCounts,
      outbound: outboundCounts,
      receipts: this.state.receipts.length,
      nextSequence: this.state.nextSequence,
      stateFile: path.basename(this.config.path),
    };
  }
}
