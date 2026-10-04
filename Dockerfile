FROM python:3.13-slim
ARG UPSTREAM_REVISION
LABEL org.opencontainers.image.title="Natter GUI" \
      org.opencontainers.image.source="https://github.com/sexyfeifan/Natter-GUI" \
      org.opencontainers.image.licenses="GPL-3.0" \
      io.natter-gui.upstream.revision="${UPSTREAM_REVISION}"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends iproute2 \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 10001 nattergui && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin nattergui \
    && mkdir /data && chown 10001:10001 /data
COPY natter_gui /app/natter_gui
COPY vendor /app/vendor
COPY upstream.lock.json LICENSE /app/
USER 10001:10001
VOLUME /data
EXPOSE 9080
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9080/healthz',timeout=3)" || exit 1
CMD ["python", "-m", "natter_gui", "--host", "0.0.0.0", "--state-dir", "/data"]
