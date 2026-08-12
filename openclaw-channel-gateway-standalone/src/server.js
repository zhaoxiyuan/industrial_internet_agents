import http from "node:http";
import { randomUUID } from "node:crypto";
import { GatewayError, errorBody } from "./core/errors.js";
import { constantTimeEqual } from "./util/crypto.js";
import {
  lowerCaseHeaders,
  parseJsonBody,
  readRequestBody,
  sendJson,
  setSecurityHeaders,
} from "./util/http.js";

function integerParam(value, fallback, { min = 0, max = Number.MAX_SAFE_INTEGER } = {}) {
  if (value === null || value === undefined || value === "") return fallback;
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < min || parsed > max) {
    throw new GatewayError("VALIDATION_ERROR", `Invalid integer query parameter: ${value}`, { status: 400 });
  }
  return parsed;
}


async function writeSse(response, chunk) {
  if (response.destroyed || response.writableEnded) return false;
  if (response.write(chunk)) return true;
  return await new Promise((resolve) => {
    const cleanup = () => {
      response.off("drain", onDrain);
      response.off("close", onClose);
      response.off("error", onClose);
    };
    const onDrain = () => {
      cleanup();
      resolve(true);
    };
    const onClose = () => {
      cleanup();
      resolve(false);
    };
    response.once("drain", onDrain);
    response.once("close", onClose);
    response.once("error", onClose);
  });
}

function booleanValue(value, fallback = false) {
  if (value === undefined || value === null) return fallback;
  if (value === true || value === "true") return true;
  if (value === false || value === "false") return false;
  throw new GatewayError("VALIDATION_ERROR", `Expected boolean, received: ${value}`, { status: 400 });
}

export class GatewayHttpServer {
  constructor({ config, gateway, registry, store, hub, logger }) {
    this.config = config;
    this.gateway = gateway;
    this.registry = registry;
    this.store = store;
    this.hub = hub;
    this.logger = logger;
    this.server = http.createServer((request, response) => void this.handle(request, response));
    this.server.requestTimeout = config.server.requestTimeoutMs + 5_000;
    this.server.headersTimeout = Math.max(10_000, config.server.requestTimeoutMs);
    this.server.keepAliveTimeout = 5_000;
  }

  async start() {
    await new Promise((resolve, reject) => {
      this.server.once("error", reject);
      this.server.listen(this.config.server.port, this.config.server.host, () => {
        this.server.off("error", reject);
        resolve();
      });
    });
    const address = this.server.address();
    return typeof address === "object" && address
      ? { host: address.address, port: address.port }
      : { host: this.config.server.host, port: this.config.server.port };
  }

  async stop() {
    await new Promise((resolve) => this.server.close(() => resolve()));
  }

  authorized(request, url) {
    const auth = request.headers.authorization;
    const bearer = typeof auth === "string" ? auth.replace(/^Bearer\s+/i, "") : "";
    let supplied = bearer;
    if (!supplied && this.config.server.allowQueryToken) {
      supplied = url.searchParams.get("access_token") ?? "";
    }
    return constantTimeEqual(this.config.server.apiKey, supplied);
  }

  async jsonRequest(request, { allowEmpty = false } = {}) {
    const rawBody = await readRequestBody(request, this.config.server.maxBodyBytes, this.config.server.requestTimeoutMs);
    return { rawBody, body: parseJsonBody(rawBody, { allowEmpty }) };
  }

  async handle(request, response) {
    const requestId = request.headers["x-request-id"] || randomUUID();
    const started = Date.now();
    setSecurityHeaders(response);
    response.setHeader("x-request-id", requestId);
    const url = new URL(request.url ?? "/", "http://gateway.local");
    try {
      if (request.method === "OPTIONS") {
        response.statusCode = 204;
        response.end();
        return;
      }
      if (request.method === "GET" && url.pathname === "/healthz") {
        sendJson(response, 200, { status: "ok", time: new Date().toISOString() });
        return;
      }
      if (request.method === "GET" && url.pathname === "/readyz") {
        const ready = this.store.ready;
        sendJson(response, ready ? 200 : 503, { status: ready ? "ready" : "not_ready", store: this.store.stats() });
        return;
      }

      const webhook = url.pathname.match(/^\/webhooks\/([^/]+)(?:\/([^/]+))?$/);
      if (request.method === "POST" && webhook) {
        const channelId = decodeURIComponent(webhook[1]);
        const accountId = decodeURIComponent(webhook[2] ?? "default");
        const { rawBody, body } = await this.jsonRequest(request);
        const result = await this.gateway.ingestWebhook(channelId, accountId, {
          headers: lowerCaseHeaders(request.headers),
          rawBody,
          body,
        });
        if (result.response) {
          sendJson(response, result.response.status ?? 200, result.response.body ?? {});
          return;
        }
        const accepted = result.accepted ?? [];
        const extraHeaders = accepted.length > 0
          ? { "x-channel-gateway-delivery-accepted": "persisted" }
          : {};
        sendJson(response, 200, {
          accepted: accepted.length > 0,
          event_ids: accepted.map((item) => item.record.id),
          duplicates: accepted.filter((item) => item.duplicate).map((item) => item.record.id),
          ignored: result.ignored,
          ignored_reason: result.ignoredReason,
        }, extraHeaders);
        return;
      }

      if (!url.pathname.startsWith("/v1/") || !this.authorized(request, url)) {
        if (url.pathname.startsWith("/v1/")) {
          throw new GatewayError("UNAUTHORIZED", "A valid Bearer API key is required", { status: 401 });
        }
        throw new GatewayError("NOT_FOUND", "Route not found", { status: 404 });
      }

      if (request.method === "GET" && url.pathname === "/v1/meta") {
        sendJson(response, 200, {
          service: "openclaw-channel-gateway-standalone",
          version: "0.1.0",
          upstream_reference: "openclaw/openclaw v2026.7.1-2",
          store: this.store.stats(),
        });
        return;
      }
      if (request.method === "GET" && url.pathname === "/v1/channels") {
        sendJson(response, 200, { channels: this.registry.list(this.config) });
        return;
      }
      if (request.method === "POST" && url.pathname === "/v1/inbound") {
        const { body } = await this.jsonRequest(request);
        const stored = await this.gateway.ingestTrusted(body);
        sendJson(response, stored.duplicate ? 200 : 202, {
          accepted: true,
          duplicate: stored.duplicate,
          event: stored.record,
        }, { "x-channel-gateway-delivery-accepted": "persisted" });
        return;
      }
      if (request.method === "GET" && url.pathname === "/v1/events") {
        const events = await this.store.listEvents({
          afterSequence: integerParam(url.searchParams.get("after_sequence"), 0, { min: 0 }),
          limit: integerParam(url.searchParams.get("limit"), 100, { min: 1, max: 1000 }),
          status: url.searchParams.get("status") || undefined,
          channel: url.searchParams.get("channel") || undefined,
          sessionKey: url.searchParams.get("session_key") || undefined,
        });
        sendJson(response, 200, { events });
        return;
      }
      if (request.method === "GET" && url.pathname === "/v1/events/stream") {
        response.statusCode = 200;
        response.setHeader("content-type", "text/event-stream; charset=utf-8");
        response.setHeader("connection", "keep-alive");
        response.setHeader("cache-control", "no-cache, no-transform");
        response.flushHeaders();
        const client = this.hub.add(response, { paused: true });
        const after = integerParam(url.searchParams.get("after_sequence") ?? request.headers["last-event-id"], 0, { min: 0 });
        const snapshotSequence = Math.max(0, this.store.stats().nextSequence - 1);
        let cursor = after;
        let replayed = 0;
        if (!await writeSse(response, `event: ready\ndata: ${JSON.stringify({ replay_from: after, snapshot_sequence: snapshotSequence })}\n\n`)) {
          this.hub.remove(client);
          return;
        }
        while (cursor < snapshotSequence) {
          const batch = await this.store.listEvents({ afterSequence: cursor, limit: 500 });
          const replay = batch.filter((event) => event.sequence <= snapshotSequence);
          if (replay.length === 0) break;
          for (const event of replay) {
            const written = await writeSse(response, `id: ${event.sequence}\nevent: inbound\ndata: ${JSON.stringify(event)}\n\n`);
            if (!written) {
              this.hub.remove(client);
              return;
            }
            cursor = event.sequence;
            replayed += 1;
          }
          if (batch.length < 500) break;
        }
        await writeSse(response, `event: replay-complete\ndata: ${JSON.stringify({ replayed, last_sequence: cursor })}\n\n`);
        this.hub.resume(client);
        return;
      }

      let match = url.pathname.match(/^\/v1\/events\/([^/]+)$/);
      if (request.method === "GET" && match) {
        const event = await this.store.getEvent(decodeURIComponent(match[1]));
        if (!event) throw new GatewayError("EVENT_NOT_FOUND", "Event not found", { status: 404 });
        sendJson(response, 200, { event });
        return;
      }
      match = url.pathname.match(/^\/v1\/events\/([^/]+)\/ack$/);
      if (request.method === "POST" && match) {
        const { body } = await this.jsonRequest(request, { allowEmpty: true });
        const status = body?.status ?? "acked";
        if (!["acked", "ignored"].includes(status)) {
          throw new GatewayError("VALIDATION_ERROR", "ACK status must be acked or ignored", { status: 400 });
        }
        const event = await this.store.ackEvent(decodeURIComponent(match[1]), {
          status,
          details: body?.details,
        });
        this.gateway.emit("event-status", event);
        sendJson(response, 200, { event });
        return;
      }
      match = url.pathname.match(/^\/v1\/events\/([^/]+)\/retry$/);
      if (request.method === "POST" && match) {
        const event = await this.store.retryEvent(decodeURIComponent(match[1]), null, Date.now());
        this.gateway.emit("event-status", event);
        sendJson(response, 202, { event });
        return;
      }
      if (request.method === "GET" && url.pathname === "/v1/dead-letters") {
        const events = await this.store.listEvents({
          status: "dead_letter",
          limit: integerParam(url.searchParams.get("limit"), 100, { min: 1, max: 1000 }),
        });
        sendJson(response, 200, { events });
        return;
      }
      match = url.pathname.match(/^\/v1\/dead-letters\/([^/]+)\/retry$/);
      if (request.method === "POST" && match) {
        const event = await this.store.retryEvent(decodeURIComponent(match[1]), null, Date.now());
        this.gateway.emit("event-status", event);
        sendJson(response, 202, { event });
        return;
      }
      if (request.method === "POST" && url.pathname === "/v1/messages/send") {
        const { body } = await this.jsonRequest(request);
        const idempotencyKey = request.headers["idempotency-key"] ?? body.idempotencyKey ?? body.idempotency_key;
        const result = await this.gateway.sendMessage({ ...body, idempotencyKey }, { idempotencyKey });
        sendJson(response, result.replayed ? 200 : 201, result);
        return;
      }
      if (request.method === "POST" && url.pathname === "/v1/messages/reply") {
        const { body } = await this.jsonRequest(request);
        if (!body?.eventId && !body?.event_id) {
          throw new GatewayError("VALIDATION_ERROR", "eventId is required", { status: 400 });
        }
        const idempotencyKey = request.headers["idempotency-key"] ?? body.idempotencyKey ?? body.idempotency_key;
        const result = await this.gateway.replyToEvent(body.eventId ?? body.event_id, {
          ...body,
          idempotencyKey,
        }, { idempotencyKey });
        sendJson(response, result.replayed ? 200 : 201, result);
        return;
      }
      if (request.method === "GET" && url.pathname === "/v1/outbound/intents") {
        const intents = await this.store.listOutboundIntents({
          status: url.searchParams.get("status") || undefined,
          limit: integerParam(url.searchParams.get("limit"), 100, { min: 1, max: 1000 }),
        });
        sendJson(response, 200, { intents });
        return;
      }
      match = url.pathname.match(/^\/v1\/outbound\/intents\/([^/]+)$/);
      if (request.method === "GET" && match) {
        const intent = await this.store.getOutboundIntent(decodeURIComponent(match[1]));
        if (!intent) throw new GatewayError("OUTBOUND_INTENT_NOT_FOUND", "Outbound intent not found", { status: 404 });
        sendJson(response, 200, { intent });
        return;
      }
      match = url.pathname.match(/^\/v1\/outbound\/intents\/([^/]+)\/retry$/);
      if (request.method === "POST" && match) {
        const { body } = await this.jsonRequest(request, { allowEmpty: true });
        const result = await this.gateway.retryOutboundIntent(decodeURIComponent(match[1]), {
          force: booleanValue(body?.force, false),
        });
        sendJson(response, result.replayed ? 200 : 201, result);
        return;
      }
      if (request.method === "GET" && url.pathname === "/v1/receipts") {
        const receipts = await this.store.listReceipts({
          limit: integerParam(url.searchParams.get("limit"), 100, { min: 1, max: 1000 }),
        });
        sendJson(response, 200, { receipts });
        return;
      }
      throw new GatewayError("NOT_FOUND", "Route not found", { status: 404 });
    } catch (error) {
      const rendered = errorBody(error, requestId);
      sendJson(response, rendered.status, rendered.body, rendered.status === 401 ? { "www-authenticate": "Bearer" } : {});
      const log = rendered.status >= 500 ? this.logger.error : this.logger.warn;
      log.call(this.logger, "request failed", {
        request_id: requestId,
        method: request.method,
        path: url.pathname,
        status: rendered.status,
        error_code: rendered.body.error.code,
      });
    } finally {
      this.logger.debug("request completed", {
        request_id: requestId,
        method: request.method,
        path: url.pathname,
        status: response.statusCode,
        duration_ms: Date.now() - started,
      });
    }
  }
}
