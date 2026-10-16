FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd --create-home player && mkdir /data && chown player:player /data
USER player
EXPOSE 5010
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5010/health',timeout=3)"
CMD ["mock-player", "serve", "--host", "0.0.0.0", "--port", "5010", "--data-dir", "/data"]
