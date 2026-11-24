#!/usr/bin/env bash
# Start the compose database on a random free port, run pytest against it, always clean up.
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
export PG_PORT="$("$PY" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')"
export POSTGRES_PASSWORD="$("$PY" -c 'import secrets; print(secrets.token_hex(12))')"
export APP_DB_PASSWORD="$("$PY" -c 'import secrets; print(secrets.token_hex(12))')"
export COMPOSE_PROJECT_NAME="rlsdemo_$$"

cleanup() { docker compose down -v --remove-orphans >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker compose up -d >/dev/null
export RLS_ADMIN_DSN="postgresql://postgres:${POSTGRES_PASSWORD}@127.0.0.1:${PG_PORT}/rls_demo"
export RLS_APP_DSN="postgresql://app_user:${APP_DB_PASSWORD}@127.0.0.1:${PG_PORT}/rls_demo"

# The init phase listens on a unix socket only, so a TCP login means init is finished.
for _ in $(seq 1 60); do
  if "$PY" -c "import psycopg,sys; psycopg.connect(sys.argv[1], connect_timeout=2).close()" "$RLS_APP_DSN" 2>/dev/null; then
    break
  fi
  sleep 1
done

"$PY" -m pytest -q "$@"
