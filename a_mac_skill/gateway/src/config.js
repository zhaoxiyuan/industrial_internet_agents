import { readFile } from "node:fs/promises";
import path from "node:path";
import { GatewayError } from "./core/errors.js";

const DEFAULT_CONFIG = {
  server: {
    host: "127.0.0.1",
    port: 8787,
    apiKey: null,
    maxBodyBytes: 1_048_576,
    requestTimeoutMs: 15_000,
    allowQueryToken: false,
  },
  storage: {
    directory: "./data",
    stateFile: "gateway-state.json",
    maxEvents: 10_000,
    maxReceipts: 10_000,
    dedupeRetentionMs: 7 * 24 * 60 * 60 * 1000,
    acknowledgedRetentionMs: 7 * 24 * 60 * 60 * 1000,
  },
  delivery: {
    callbackUrl: null,
    callbackToken: null,
    timeoutMs: 30_000,
    maxResponseBytes: 1_048_576,
    concurrency: 4,
    pollIntervalMs: 500,
    maxAttempts: 5,
    baseDelayMs: 1_000,
    maxDelayMs: 60_000,
    autoAck: true,
  },
  channels: {
    loopback: {
      enabled: true,
      accounts: {
        default: {
          webhookToken: null,
        },
      },
    },
    generic: {
      enabled: false,
      accounts: {},
    },
    feishu: {
      enabled: false,
      accounts: {},
    },
  },
};

function isPlainObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function deepMerge(base, override) {
  if (!isPlainObject(base) || !isPlainObject(override)) {
    return structuredClone(override);
  }
  const result = structuredClone(base);
  for (const [key, value] of Object.entries(override)) {
    result[key] = isPlainObject(value) && isPlainObject(result[key])
      ? deepMerge(result[key], value)
      : structuredClone(value);
  }
  return result;
}

function interpolateString(value, env) {
  return value.replace(/\$\{([A-Z_][A-Z0-9_]*)\}/g, (_, name) => {
    if (!(name in env) || env[name] === "") {
      throw new GatewayError("CONFIG_ENV_MISSING", `Required environment variable is not set: ${name}`, { status: 500 });
    }
    return env[name];
  });
}

function interpolateEnvironment(value, env) {
  if (typeof value === "string") return interpolateString(value, env);
  if (Array.isArray(value)) return value.map((item) => interpolateEnvironment(item, env));
  if (isPlainObject(value)) {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, interpolateEnvironment(item, env)]));
  }
  return value;
}

function validatePositiveInteger(value, label, { min = 1, max = Number.MAX_SAFE_INTEGER } = {}) {
  if (!Number.isInteger(value) || value < min || value > max) {
    throw new GatewayError("CONFIG_INVALID", `${label} must be an integer between ${min} and ${max}`, { status: 500 });
  }
}

function validateConfig(config) {
  if (typeof config.server.host !== "string" || config.server.host.trim() === "") {
    throw new GatewayError("CONFIG_INVALID", "server.host is required", { status: 500 });
  }
  validatePositiveInteger(config.server.port, "server.port", { max: 65535 });
  validatePositiveInteger(config.server.maxBodyBytes, "server.maxBodyBytes", { min: 1024, max: 100 * 1024 * 1024 });
  validatePositiveInteger(config.server.requestTimeoutMs, "server.requestTimeoutMs", { min: 100, max: 300_000 });
  if (typeof config.server.apiKey !== "string" || config.server.apiKey.length < 16) {
    throw new GatewayError("CONFIG_INVALID", "server.apiKey must be at least 16 characters; set CG_API_KEY or configure it explicitly", { status: 500 });
  }
  validatePositiveInteger(config.storage.maxEvents, "storage.maxEvents", { min: 100, max: 1_000_000 });
  validatePositiveInteger(config.storage.maxReceipts, "storage.maxReceipts", { min: 100, max: 1_000_000 });
  validatePositiveInteger(config.delivery.concurrency, "delivery.concurrency", { min: 1, max: 128 });
  validatePositiveInteger(config.delivery.maxAttempts, "delivery.maxAttempts", { min: 1, max: 100 });
  validatePositiveInteger(config.delivery.pollIntervalMs, "delivery.pollIntervalMs", { min: 50, max: 60_000 });

  for (const [channelId, channel] of Object.entries(config.channels ?? {})) {
    if (!isPlainObject(channel)) {
      throw new GatewayError("CONFIG_INVALID", `channels.${channelId} must be an object`, { status: 500 });
    }
    if (channel.enabled !== false && !isPlainObject(channel.accounts)) {
      throw new GatewayError("CONFIG_INVALID", `channels.${channelId}.accounts must be an object`, { status: 500 });
    }
  }
  return config;
}

export async function loadConfig({ configPath, env = process.env } = {}) {
  const selectedPath = configPath ?? env.CG_CONFIG ?? null;
  let fileConfig = {};
  let baseDirectory = process.cwd();
  if (selectedPath) {
    const absolutePath = path.resolve(selectedPath);
    baseDirectory = path.dirname(absolutePath);
    let raw;
    try {
      raw = await readFile(absolutePath, "utf8");
    } catch (error) {
      throw new GatewayError("CONFIG_READ_FAILED", `Unable to read config file: ${absolutePath}`, { status: 500, cause: error });
    }
    try {
      fileConfig = JSON.parse(raw);
    } catch (error) {
      throw new GatewayError("CONFIG_INVALID_JSON", `Config file is not valid JSON: ${absolutePath}`, { status: 500, cause: error });
    }
  }

  let config = deepMerge(DEFAULT_CONFIG, interpolateEnvironment(fileConfig, env));
  if (env.CG_API_KEY) config.server.apiKey = env.CG_API_KEY;
  if (env.CG_HOST) config.server.host = env.CG_HOST;
  if (env.CG_PORT) config.server.port = Number(env.CG_PORT);
  if (env.CG_AGENT_CALLBACK_URL) config.delivery.callbackUrl = env.CG_AGENT_CALLBACK_URL;
  if (env.CG_AGENT_CALLBACK_TOKEN) config.delivery.callbackToken = env.CG_AGENT_CALLBACK_TOKEN;

  config.storage.directory = path.resolve(baseDirectory, config.storage.directory);
  config.storage.path = path.resolve(config.storage.directory, config.storage.stateFile);
  config.meta = {
    configPath: selectedPath ? path.resolve(selectedPath) : null,
    baseDirectory,
  };
  return validateConfig(config);
}

export function redactConfig(config) {
  const clone = structuredClone(config);
  const secretKeys = new Set([
    "apiKey", "callbackToken", "webhookToken", "webhookSecret", "outboundBearerToken",
    "appSecret", "verificationToken", "encryptKey",
  ]);
  function redact(value) {
    if (!value || typeof value !== "object") return;
    for (const [key, item] of Object.entries(value)) {
      if (secretKeys.has(key) && item) {
        value[key] = "***redacted***";
      } else {
        redact(item);
      }
    }
  }
  redact(clone);
  return clone;
}
