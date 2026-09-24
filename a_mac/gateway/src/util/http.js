import { GatewayError, PlatformSendError } from "../core/errors.js";

export function lowerCaseHeaders(headers) {
  const output = {};
  for (const [key, value] of Object.entries(headers ?? {})) {
    output[String(key).toLowerCase()] = Array.isArray(value) ? value.join(",") : String(value ?? "");
  }
  return output;
}

export async function readRequestBody(request, maxBytes, timeoutMs = 15_000) {
  return await new Promise((resolve, reject) => {
    const chunks = [];
    let total = 0;
    let settled = false;
    const timer = setTimeout(() => {
      if (!settled) {
        settled = true;
        request.resume();
        reject(new GatewayError("REQUEST_TIMEOUT", "Request body timed out", { status: 408 }));
      }
    }, timeoutMs);

    request.on("data", (chunk) => {
      if (settled) return;
      total += chunk.length;
      if (total > maxBytes && !settled) {
        settled = true;
        clearTimeout(timer);
        request.resume();
        reject(new GatewayError("BODY_TOO_LARGE", `Request body exceeds ${maxBytes} bytes`, { status: 413 }));
        return;
      }
      chunks.push(chunk);
    });
    request.on("end", () => {
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        resolve(Buffer.concat(chunks));
      }
    });
    request.on("error", (error) => {
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        reject(new GatewayError("REQUEST_READ_FAILED", "Unable to read request body", { status: 400, cause: error }));
      }
    });
  });
}

export function parseJsonBody(rawBody, { allowEmpty = false } = {}) {
  if (rawBody.length === 0) {
    if (allowEmpty) {
      return null;
    }
    throw new GatewayError("EMPTY_BODY", "JSON request body is required", { status: 400 });
  }
  try {
    return JSON.parse(rawBody.toString("utf8"));
  } catch (error) {
    throw new GatewayError("INVALID_JSON", "Request body is not valid JSON", { status: 400, cause: error });
  }
}

export function setSecurityHeaders(response) {
  response.setHeader("x-content-type-options", "nosniff");
  response.setHeader("x-frame-options", "DENY");
  response.setHeader("referrer-policy", "no-referrer");
  response.setHeader("cache-control", "no-store");
}

export function sendJson(response, status, body, extraHeaders = {}) {
  const payload = Buffer.from(JSON.stringify(body), "utf8");
  response.statusCode = status;
  response.setHeader("content-type", "application/json; charset=utf-8");
  response.setHeader("content-length", payload.length);
  for (const [key, value] of Object.entries(extraHeaders)) {
    response.setHeader(key, value);
  }
  response.end(payload);
}

async function readFetchBodyLimited(response, maxBytes) {
  if (!response.body) {
    return Buffer.alloc(0);
  }
  const reader = response.body.getReader();
  const chunks = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > maxBytes) {
        await reader.cancel();
        throw new PlatformSendError("UPSTREAM_RESPONSE_TOO_LARGE", `Upstream response exceeds ${maxBytes} bytes`, {
          ambiguous: true,
          retryable: false,
          safeToRetry: false,
        });
      }
      chunks.push(Buffer.from(value));
    }
  } finally {
    reader.releaseLock();
  }
  return Buffer.concat(chunks);
}

export function assertStaticHttpUrl(rawUrl, label = "URL") {
  let parsed;
  try {
    parsed = new URL(rawUrl);
  } catch (error) {
    throw new GatewayError("INVALID_URL", `${label} is not a valid URL`, { status: 500, cause: error });
  }
  if (!["http:", "https:"].includes(parsed.protocol)) {
    throw new GatewayError("INVALID_URL", `${label} must use http or https`, { status: 500 });
  }
  if (parsed.username || parsed.password) {
    throw new GatewayError("INVALID_URL", `${label} must not contain embedded credentials`, { status: 500 });
  }
  return parsed.toString();
}

export async function fetchJson(url, options = {}) {
  const {
    method = "GET",
    headers = {},
    body,
    timeoutMs = 15_000,
    maxResponseBytes = 1_048_576,
    operation = "upstream request",
    classifyHttpError,
  } = options;
  const staticUrl = assertStaticHttpUrl(url, operation);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new Error(`${operation} timed out`)), timeoutMs);
  let response;
  try {
    response = await fetch(staticUrl, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
      redirect: "error",
    });
  } catch (error) {
    const aborted = controller.signal.aborted;
    throw new PlatformSendError(
      aborted ? "UPSTREAM_TIMEOUT" : "UPSTREAM_NETWORK_ERROR",
      aborted ? `${operation} timed out` : `${operation} failed before a response was received`,
      {
        cause: error,
        retryable: false,
        ambiguous: true,
        safeToRetry: false,
      },
    );
  } finally {
    clearTimeout(timer);
  }
  const raw = await readFetchBodyLimited(response, maxResponseBytes);
  let parsed = null;
  if (raw.length > 0) {
    try {
      parsed = JSON.parse(raw.toString("utf8"));
    } catch {
      parsed = { raw: raw.toString("utf8").slice(0, 4096) };
    }
  }
  if (!response.ok) {
    const classification = classifyHttpError?.(response.status, parsed) ?? {
      safeToRetry: response.status >= 400 && response.status < 500,
      ambiguous: response.status >= 500,
      retryable: response.status >= 500,
    };
    throw new PlatformSendError("UPSTREAM_HTTP_ERROR", `${operation} returned HTTP ${response.status}`, {
      status: 502,
      details: { upstream_status: response.status, upstream_body: parsed },
      ...classification,
    });
  }
  return { status: response.status, headers: response.headers, data: parsed };
}
