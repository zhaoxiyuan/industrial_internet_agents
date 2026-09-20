import { ChannelRegistry } from "./core/registry.js";
import { JsonStateStore } from "./core/store.js";
import { SseHub } from "./core/sse-hub.js";
import { ChannelGateway } from "./core/gateway.js";
import { DeliveryWorker } from "./core/delivery-worker.js";
import { GatewayHttpServer } from "./server.js";
import { LoopbackAdapter } from "./adapters/loopback.js";
import { GenericWebhookAdapter } from "./adapters/generic-webhook.js";
import { FeishuAdapter } from "./adapters/feishu.js";

export async function createApplication({ config, logger }) {
  const registry = new ChannelRegistry();
  registry.register(new LoopbackAdapter(logger));
  registry.register(new GenericWebhookAdapter(logger));
  registry.register(new FeishuAdapter(logger));

  const store = new JsonStateStore(config.storage, logger);
  await store.init();
  const hub = new SseHub(logger);
  const gateway = new ChannelGateway({ config, registry, store, logger });
  gateway.on("event", (event) => hub.publish("inbound", event, event.sequence));
  gateway.on("event-status", (event) => hub.publish("event-status", event, event.sequence));
  gateway.on("receipt", (receipt) => hub.publish("receipt", receipt));
  gateway.on("outbound", (intent) => hub.publish("outbound", intent));

  const worker = new DeliveryWorker({ config: config.delivery, gateway, store, logger });
  const server = new GatewayHttpServer({ config, gateway, registry, store, hub, logger });

  return {
    config,
    registry,
    store,
    hub,
    gateway,
    worker,
    server,
    async start() {
      const address = await server.start();
      worker.start();
      return address;
    },
    async stop() {
      worker.stop();
      hub.close();
      await server.stop();
    },
  };
}
