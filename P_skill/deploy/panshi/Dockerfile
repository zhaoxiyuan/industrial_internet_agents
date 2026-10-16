FROM python:3.12-slim
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ENV PIP_INDEX_URL=${PIP_INDEX_URL} PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/industrial-agents
COPY platform/requirements.lock.txt /tmp/platform-requirements.txt
RUN pip install --no-cache-dir -r /tmp/platform-requirements.txt
COPY platform /opt/industrial-agents/platform
COPY P_skill/runtime /opt/industrial-agents/P_skill/runtime
COPY P_skill/Docker/mock_player /opt/industrial-agents/P_skill/Docker/mock_player
RUN pip install --no-cache-dir ./P_skill/runtime ./P_skill/Docker/mock_player
CMD ["python", "platform/app.py", "--port", "18770"]
