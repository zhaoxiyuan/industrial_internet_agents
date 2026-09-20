# a/deploy/Dockerfile.p6 — P6 监测 + A5 + A6 + P7 调度(FastAPI :5002)
#
# 入口命令来自 agents/p6_monitor_agent.py 的 main(),
# CLI 参数:--host 0.0.0.0 --port 5002(2 行已加在 a/agents/p6_monitor_agent.py 的 main() 里)。
# 健康检查:由我们新增的 GET /api/health 路由处理(a/agents/p6_monitor_agent.py 已加)。

FROM a-deploy-base

ARG http_proxy
ARG https_proxy
ARG no_proxy

# 1. 装 a/ 全部依赖 + 缺失的 fastapi/uvicorn/flask
COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt \
 && pip install --no-cache-dir \
        'fastapi>=0.110' \
        'uvicorn[standard]>=0.27' \
        'flask>=3.0'

# 2. COPY 必要的源码(精确控制层,避免无关变更触发 rebuild)
COPY agents/         ./agents/
COPY A5/             ./A5/
COPY A6_A7/          ./A6_A7/
COPY A7/             ./A7/
COPY P8P9/           ./P8P9/
COPY feishu_gateway_cli/ ./feishu_gateway_cli/
COPY frontend/       ./frontend/
COPY data/           ./data/
COPY agent_config/   ./agent_config/
COPY .env            ./.env

EXPOSE 5002

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:5002/api/health || exit 1

# uvicorn 跑 FastAPI,workers=1(P6 内部用 _monitor_mutex 单 job)
# 注:agents/p6_monitor_agent.py 已支持 --host/--port
CMD ["python", "agents/p6_monitor_agent.py", "--host", "0.0.0.0", "--port", "5002"]
