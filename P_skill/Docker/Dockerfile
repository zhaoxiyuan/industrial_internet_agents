FROM a-p10:0.1.0
USER root
COPY P_skill/runtime /opt/industrial-runtime
COPY P_skill/Docker/mock_player /opt/mock-player
COPY original /opt/original
RUN pip install --no-cache-dir /opt/industrial-runtime /opt/mock-player
ENV P10_ORIGINAL_ROOT=/opt/original
WORKDIR /opt/original
ENTRYPOINT []
CMD ["python", "-c", "import time; time.sleep(2147483647)"]
