#!/usr/bin/env node
import { loadConfig } from "./config.js";
import { createLogger } from "./logger.js";
import { createApplication } from "./app.js";

function parseArguments(argv) {
  const args = { configPath: undefined };
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "--config" || value === "-c") {
      args.configPath = argv[++index];
    } else if (value === "--help" || value === "-h") {
      args.help = true;
    } else if (value === "--version" || value === "-v") {
      args.version = true;
    } else {
      throw new Error(`Unknown argument: ${value}`);
    }
  }
  return args;
}

const args = parseArguments(process.argv.slice(2));
if (args.help) {
  process.stdout.write(`openclaw-channel-gateway-standalone 0.1.0\n\nUsage:\n  node src/index.js --config ./config/config.json\n\nEnvironment:\n  CG_CONFIG, CG_API_KEY, CG_HOST, CG_PORT, CG_AGENT_CALLBACK_URL, CG_AGENT_CALLBACK_TOKEN\n`);
  process.exit(0);
}
if (args.version) {
  process.stdout.write("0.1.0\n");
  process.exit(0);
}

const logger = createLogger();
let application;
try {
  const config = await loadConfig({ configPath: args.configPath });
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
