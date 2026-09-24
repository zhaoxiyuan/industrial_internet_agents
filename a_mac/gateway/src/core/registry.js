import { GatewayError } from "./errors.js";

export class ChannelRegistry {
  constructor() {
    this.adapters = new Map();
    this.aliases = new Map();
  }

  register(adapter) {
    if (!adapter || typeof adapter.id !== "string" || typeof adapter.receive !== "function" || typeof adapter.send !== "function") {
      throw new TypeError("Channel adapter must expose id, receive(), and send()");
    }
    const id = adapter.id.toLowerCase();
    if (this.adapters.has(id)) {
      throw new Error(`Channel adapter already registered: ${id}`);
    }
    this.adapters.set(id, adapter);
    for (const alias of adapter.meta?.aliases ?? []) {
      this.aliases.set(String(alias).toLowerCase(), id);
    }
    return adapter;
  }

  get(rawId) {
    const normalized = String(rawId ?? "").trim().toLowerCase();
    const id = this.aliases.get(normalized) ?? normalized;
    const adapter = this.adapters.get(id);
    if (!adapter) {
      throw new GatewayError("CHANNEL_NOT_FOUND", `Unknown channel: ${rawId}`, { status: 404 });
    }
    return adapter;
  }

  list(config) {
    return [...this.adapters.values()].map((adapter) => {
      const channelConfig = config.channels?.[adapter.id] ?? {};
      return {
        id: adapter.id,
        label: adapter.meta?.label ?? adapter.id,
        aliases: adapter.meta?.aliases ?? [],
        enabled: channelConfig.enabled !== false,
        accounts: Object.keys(channelConfig.accounts ?? {}),
        capabilities: adapter.capabilities ?? {},
      };
    });
  }
}
