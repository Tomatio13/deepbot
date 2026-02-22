FROM golang:1.25-bookworm AS gogcli-builder

ARG GOGCLI_VERSION=latest

RUN go install github.com/steipete/gogcli/cmd/gog@${GOGCLI_VERSION}

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY . /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm bubblewrap ripgrep socat curl wget\
    && npm install -g @anthropic-ai/sandbox-runtime \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY --from=gogcli-builder /go/bin/gog /usr/local/bin/gog

RUN chmod +x /app/scripts/docker-entrypoint.sh
RUN pip install --no-cache-dir .

ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
CMD ["deepbot"]
