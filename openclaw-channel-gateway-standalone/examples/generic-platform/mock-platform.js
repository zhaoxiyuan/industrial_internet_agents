#!/usr/bin/env node
import http from "node:http";
import { timingSafeEqual } from "node:crypto";

const port = Number(process.env.MOCK_PLATFORM_PORT ?? 9000);
const expectedToken = process.env.GENERIC_OUTBOUND_TOKEN ?? "replace-generic-outbound-token";
const deliveries = new Map();

function equal(left, right) {
  const a = Buffer.from(String(left ?? ""));
  const b = Buffer.from(String(right ?? ""));
  if (a.length !== b.length) return false;
  return timingSafeEqual(a, b);
}

const server = http.createServer(async (request, response) => {
  if (request.method !== "POST" || request.url !== "/platform/send") {
    response.writeHead(404).end();
    return;
  }
  const bearer = String(request.headers.authorization ?? "").replace(/^Bearer\s+/i, "");
  if (!equal(expectedToken, bearer)) {
    response.writeHead(401, { "content-type": "application/json" });
    response.end(JSON.stringify({ error: "unauthorized" }));
    return;
  }
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  const payload = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  const deliveryId = request.headers["x-cg-delivery-id"] ?? payload.delivery_id;
  let result = deliveries.get(deliveryId);
  if (!result) {
    result = {
      message_id: `mock-${deliveryId}`,
      conversation_id: payload.to?.conversation_id,
    };
    deliveries.set(deliveryId, result);
    process.stdout.write(`${JSON.stringify({ received: payload, result })}\n`);
  }
  response.writeHead(200, { "content-type": "application/json" });
  response.end(JSON.stringify(result));
});

server.listen(port, "127.0.0.1", () => {
  process.stdout.write(`mock generic platform listening on http://127.0.0.1:${port}\n`);
});
