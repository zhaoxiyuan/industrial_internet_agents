// start.mjs — 加载 .env，然后启动网关
import { readFileSync } from "fs";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";
import { loadConfig } from "./src/config.js";
import { createLogger } from "./src/logger.js";
import { createApplication } from "./src/app.js";

const __dirname = dirname(fileURLToPath(import.meta.url));

// 1. 加载 .env 到 process.env
const envPath = resolve(__dirname, ".env");
const envContent = readFileSync(envPath, "utf8");
for (const line of envContent.split("\n")) {
  const trimmed = line.trim();
  if (!trimmed || trimmed.startsWith("#")) continue;
  const eqIdx = trimmed.indexOf("=");
  if (eqIdx < 0) continue;
  const key = trimmed.slice(0, eqIdx).trim();
  const value = trimmed.slice(eqIdx + 1).trim();
  if (key) process.env[key] = value;
}

// 2. 解析 --config 参数
const configArgIdx = process.argv.findIndex((a) => a === "--config" || a === "-c");
const configPath = configArgIdx >= 0 ? process.argv[configArgIdx + 1] : undefined;

// 3. 启动
const logger = createLogger();
let application;
try {
  const config = await loadConfig({ configPath });
  application = await createApplication({ config, logger });
  const address = await application.start();
  logger.info("channel gateway started", {
    host: address.host,
    port: address.port,
    state_file: config.storage.path,
    callback_enabled: Boolean(config.delivery.callbackUrl),
  });
} catch (error) {
  logger.error("channel gateway failed to start", {
    error: error instanceof Error ? error.message : String(error),
    code: error?.code,
  });
  process.exit(1);
}

// 4. 优雅退出
let stopping = false;
async function shutdown(signal) {
  if (stopping) return;
  stopping = true;
  logger.info("channel gateway stopping", { signal });
  try {
    await application.stop();
    process.exit(0);
  } catch (error) {
    logger.error("channel gateway shutdown failed", { error: error instanceof Error ? error.message : String(error) });
    process.exit(1);
  }
}
process.on("SIGINT", () => void shutdown("SIGINT"));
process.on("SIGTERM", () => void shutdown("SIGTERM"));
process.on("unhandledRejection", (error) => {
  logger.error("unhandled rejection", { error: error instanceof Error ? error.message : String(error) });
});
process.on("uncaughtException", (error) => {
  logger.error("uncaught exception", { error: error.message });
  void shutdown("uncaughtException");
});
