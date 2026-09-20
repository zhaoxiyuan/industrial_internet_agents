const DEFAULT_MAX_BUFFERED_BYTES = 1_048_576;

export class SseHub {
  constructor(logger, { maxBufferedBytes = DEFAULT_MAX_BUFFERED_BYTES } = {}) {
    this.logger = logger;
    this.maxBufferedBytes = maxBufferedBytes;
    this.clients = new Set();
    this.heartbeat = setInterval(() => {
      for (const client of this.clients) {
        this.write(client, `: heartbeat ${Date.now()}\n\n`);
      }
    }, 15_000);
    this.heartbeat.unref();
  }

  add(response, { paused = false } = {}) {
    const client = {
      response,
      paused,
      queued: [],
      queuedBytes: 0,
      closed: false,
    };
    this.clients.add(client);
    const close = () => this.remove(client);
    response.on("close", close);
    response.on("error", close);
    return client;
  }

  remove(client) {
    if (!client || client.closed) return;
    client.closed = true;
    client.queued = [];
    client.queuedBytes = 0;
    this.clients.delete(client);
  }

  write(client, chunk) {
    if (!client || client.closed || client.response.destroyed || client.response.writableEnded) {
      this.remove(client);
      return false;
    }
    const bytes = Buffer.byteLength(chunk);
    if (client.paused) {
      client.queued.push(chunk);
      client.queuedBytes += bytes;
      if (client.queuedBytes > this.maxBufferedBytes) {
        this.logger.warn("closing slow SSE client during replay", { queued_bytes: client.queuedBytes });
        client.response.destroy();
        this.remove(client);
        return false;
      }
      return true;
    }
    try {
      const accepted = client.response.write(chunk);
      if (!accepted && client.response.writableLength > this.maxBufferedBytes) {
        this.logger.warn("closing slow SSE client", { writable_bytes: client.response.writableLength });
        client.response.destroy();
        this.remove(client);
        return false;
      }
      return true;
    } catch {
      this.remove(client);
      return false;
    }
  }

  resume(client) {
    if (!client || client.closed) return;
    client.paused = false;
    const queued = client.queued;
    client.queued = [];
    client.queuedBytes = 0;
    for (const chunk of queued) {
      if (!this.write(client, chunk)) break;
    }
  }

  publish(eventName, payload, id) {
    const encoded = JSON.stringify(payload);
    const chunk = `${id !== undefined ? `id: ${id}\n` : ""}event: ${eventName}\ndata: ${encoded}\n\n`;
    for (const client of this.clients) {
      this.write(client, chunk);
    }
  }

  close() {
    clearInterval(this.heartbeat);
    for (const client of [...this.clients]) {
      try {
        client.response.end();
      } finally {
        this.remove(client);
      }
    }
  }
}
