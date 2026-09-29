FROM python:3.12-slim
WORKDIR /app
COPY app.py /app/app.py
COPY package_validation.py package_store.py mcp_runtime.py /app/
COPY schemas /app/schemas
COPY web /app/web
COPY requirements-agent.txt /app/requirements-agent.txt
RUN pip install --no-cache-dir -r /app/requirements-agent.txt
ENV SKILL_HOST=0.0.0.0 SKILL_PORT=8765 SKILL_DB_PATH=/data/skill.db
VOLUME /data
EXPOSE 8765
CMD ["python", "/app/app.py"]
