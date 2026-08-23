#!/bin/sh
set -eu

case "${1:-}" in
  build|ingest|transform|validate|promote|checkpoint|app)
    if ! awk '$5 == "/data" { found = 1 } END { exit !found }' /proc/self/mountinfo; then
      echo "Error: /data is not mounted. Start this image with Docker Compose (make docker-build)." >&2
      exit 64
    fi
    ;;
esac

if [ "${1:-}" = "app" ]; then
  shift
  exec uv run --no-sync shiny run --host 0.0.0.0 --port "${IMDB_DUCKLAKE_APP_PORT:-8000}" \
    apps/shiny/app.py "$@"
fi

exec uv run --no-sync imdb-lakehouse "$@"
