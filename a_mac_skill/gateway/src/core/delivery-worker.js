import { GatewayError, asGatewayError } from "./errors.js";
import { fetchJson } from "../util/http.js";

function serializedError(error) {
  const converted = asGatewayError(error);
  return {
    code: converted.code,
    message: converted.message,
    retryable: Boolean(converted.retryable),
    ambiguous: Boolean(converted.ambiguous),
    details: converted.details,
  };
}

export class DeliveryWorker {
  constructor({ config, gateway, store, logger }) {
    this.config = config;
    this.gateway = gateway;
    this.store = store;
    this.logger = logger;
    this.timer = null;
    this.running = false;
    this.active = 0;
    this.activeSessions = new Set();
  }

  start() {
    if (!this.config.callbackUrl || this.running) return;
    this.running = true;
    this.timer = setInterval(() => void this.tick(), this.config.pollIntervalMs);
    this.timer.unref();
    void this.tick();
    this.logger.info("agent callback delivery enabled", { callback_url: this.config.callbackUrl });
  }

  stop() {
    this.running = false;
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  async tick() {
    if (!this.running) return;
    while (this.active < this.config.concurrency) {
      const event = await this.store.claimNextDueEvent(this.activeSessions);
      if (!event) break;
      this.active += 1;
      this.activeSessions.add(event.session.key);
      void this.process(event).finally(() => {
        this.active -= 1;
        this.activeSessions.delete(event.session.key);
        if (this.running) queueMicrotask(() => void this.tick());
      });
    }
  }

  async process(event) {
    try {
      const headers = {
        "content-type": "application/json",
        "user-agent": "openclaw-channel-gateway-standalone/0.1",
        "x-cg-event-id": event.id,
        "x-cg-delivery-attempt": String(event.delivery.attempts),
      };
      if (this.config.callbackToken) {
        headers.authorization = `Bearer ${this.config.callbackToken}`;
      }
      const response = await fetchJson(this.config.callbackUrl, {
        method: "POST",
        headers,
        body: {
          version: "1.0",
          type: "channel.inbound",
          event,
        },
        timeoutMs: this.config.timeoutMs,
        maxResponseBytes: this.config.maxResponseBytes,
        operation: "agent callback",
        classifyHttpError(status) {
          return {
            safeToRetry: status >= 400 && status < 500,
            ambiguous: false,
            retryable: status >= 500,
          };
        },
      });
      const result = response.data ?? {};
      if (result && typeof result !== "object") {
        throw new GatewayError("AGENT_RESPONSE_INVALID", "Agent callback response must be a JSON object", { status: 502 });
      }
      const messages = Array.isArray(result.messages) ? result.messages : [];
      const receipts = [];
      for (let index = 0; index < messages.length; index += 1) {
        const message = messages[index];
        const sent = await this.gateway.replyToEvent(event.id, {
          ...message,
          idempotencyKey: message.idempotencyKey ?? `agent:${event.id}:${index}`,
        });
        receipts.push(sent.receipt);
      }
      if (result.ack === false || (!this.config.autoAck && result.ack !== true)) {
        throw new GatewayError("AGENT_NACK", "Agent callback did not acknowledge the event", { status: 502, retryable: true });
      }
      const updated = await this.store.ackEvent(event.id, {
        details: { callback_status: response.status, receipt_ids: receipts.map((receipt) => receipt?.id).filter(Boolean) },
      });
      this.gateway.emit("event-status", updated);
      this.logger.info("inbound event delivered to agent", {
        event_id: event.id,
        attempt: event.delivery.attempts,
        replies: receipts.length,
      });
    } catch (error) {
      const converted = asGatewayError(error);
      const upstreamStatus = Number(converted.details?.upstream_status);
      const permanent = upstreamStatus >= 400 && upstreamStatus < 500 && converted.code === "UPSTREAM_HTTP_ERROR";
      if (permanent || event.delivery.attempts >= this.config.maxAttempts) {
        const updated = await this.store.deadLetterEvent(event.id, serializedError(converted));
        this.gateway.emit("event-status", updated);
        this.logger.error("inbound event moved to dead letter", {
          event_id: event.id,
          attempts: event.delivery.attempts,
          error_code: converted.code,
        });
        return;
      }
      const delay = Math.min(
        this.config.maxDelayMs,
        this.config.baseDelayMs * (2 ** Math.max(0, event.delivery.attempts - 1)),
      );
      const next = Date.now() + delay;
      const updated = await this.store.retryEvent(event.id, serializedError(converted), next);
      this.gateway.emit("event-status", updated);
      this.logger.warn("inbound event callback scheduled for retry", {
        event_id: event.id,
        attempts: event.delivery.attempts,
        delay_ms: delay,
        error_code: converted.code,
      });
    }
  }
}
