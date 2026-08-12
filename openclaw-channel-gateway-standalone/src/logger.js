const LEVELS = { debug: 10, info: 20, warn: 30, error: 40 };

export function createLogger({ level = process.env.CG_LOG_LEVEL ?? "info", stream = process.stdout } = {}) {
  const threshold = LEVELS[level] ?? LEVELS.info;
  function write(logLevel, message, fields = {}) {
    if ((LEVELS[logLevel] ?? 100) < threshold) return;
    const entry = {
      time: new Date().toISOString(),
      level: logLevel,
      message,
      ...fields,
    };
    stream.write(`${JSON.stringify(entry)}\n`);
  }
  return {
    debug: (message, fields) => write("debug", message, fields),
    info: (message, fields) => write("info", message, fields),
    warn: (message, fields) => write("warn", message, fields),
    error: (message, fields) => write("error", message, fields),
  };
}
