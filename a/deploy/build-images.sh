#!/usr/bin/env sh
# Build every image for the a/ service without reading files outside a/.
set -eu

DEPLOY_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
APP_DIR=$(CDPATH= cd -- "$DEPLOY_DIR/.." && pwd)
NETWORK=${DOCKER_BUILD_NETWORK:-host}

build() {
  image=$1
  dockerfile=$2
  docker build --network "$NETWORK" \
    --build-arg "http_proxy=${http_proxy:-}" \
    --build-arg "https_proxy=${https_proxy:-}" \
    --build-arg "no_proxy=${no_proxy:-}" \
    -f "$DEPLOY_DIR/$dockerfile" -t "$image:latest" "$APP_DIR"
}

build_gateway() {
  docker build --network "$NETWORK" \
    --build-arg "http_proxy=${http_proxy:-}" \
    --build-arg "https_proxy=${https_proxy:-}" \
    --build-arg "no_proxy=${no_proxy:-}" \
    -f "$APP_DIR/gateway/Dockerfile" -t a-gateway:latest "$APP_DIR/gateway"
}

build a-deploy-base Dockerfile.base
build_gateway
build a-p6 Dockerfile.p6
build a-p8p9 Dockerfile.p8p9
build a-app-config Dockerfile.app_config
build a-feishu-cfg Dockerfile.feishu_cfg
build a-webui Dockerfile.webui
