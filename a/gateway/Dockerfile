FROM node:22-alpine

WORKDIR /app
ENV NODE_ENV=production

COPY --chown=node:node package.json ./
COPY --chown=node:node src ./src
COPY --chown=node:node config ./config
RUN mkdir -p /app/data && chown node:node /app/data
COPY --chown=node:node LICENSE THIRD_PARTY_NOTICES.md UPSTREAM_LICENSE_OPENCLAW ./

USER node
EXPOSE 8787
VOLUME ["/app/data"]

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:8787/healthz').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"

CMD ["node", "src/index.js", "--config", "config/config.container.json"]
