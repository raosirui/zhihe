#!/usr/bin/env bash
# Explicit foreground launch; never manage MySQL or another process's PID.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
usage() {
  printf '%s\n' 'Usage: ./start.sh dev|prod' \
    'dev: loopback Flask + Vite; prod: Gunicorn + existing frontend/dist.' \
    'Database/schema/storage setup is separate; no services are initialized here.'
}
if [[ $# -ne 1 ]]; then usage >&2; exit 64; fi
case "$1" in
  -h|--help) usage; exit 0 ;;
  dev|prod) ;;
  *) usage >&2; exit 64 ;;
esac
PYTHON="${PYTHON_BIN:-$ROOT/backend/.venv/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
  printf '%s\n' 'Set PYTHON_BIN to a Python interpreter with backend dependencies installed.' >&2
  exit 1
fi
if [[ "$1" == prod ]]; then
  if [[ "${APP_ENV:-}" != production ]]; then
    printf '%s\n' 'prod requires explicit APP_ENV=production (strong key, no debug/demo/simulation).' >&2
    exit 1
  fi
  if [[ ! -f "$ROOT/frontend/dist/index.html" ]]; then
    printf '%s\n' 'Build frontend first: cd frontend && npm ci && npm run build' >&2
    exit 1
  fi
  # A reverse proxy/TLS terminator may expose this loopback service. No Vite preview.
  exec "$PYTHON" -m gunicorn --chdir "$ROOT/backend" \
    --bind "${GUNICORN_BIND:-127.0.0.1:6000}" --workers "${GUNICORN_WORKERS:-2}" \
    --access-logfile - --error-logfile - 'wsgi:create_app()'
fi
export APP_ENV="${APP_ENV:-development}"
if [[ "$APP_ENV" != development ]]; then
  printf '%s\n' 'dev requires APP_ENV=development.' >&2
  exit 1
fi
children=()
cleanup() {
  trap - EXIT INT TERM
  for pid in "${children[@]}"; do
    # These are exactly the children launched in this shell, never saved PIDs.
    if kill -0 "$pid" 2>/dev/null; then kill "$pid" 2>/dev/null || true; fi
  done
  for pid in "${children[@]}"; do wait "$pid" 2>/dev/null || true; done
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
(cd "$ROOT/backend" && exec "$PYTHON" -m flask --app app:create_app run --no-reload --host 127.0.0.1 --port 6000) &
children+=("$!")
(cd "$ROOT/frontend" && exec node "$ROOT/frontend/node_modules/vite/bin/vite.js" --host 127.0.0.1 --port 4000 --strictPort) &
children+=("$!")
printf '%s\n' 'Development: frontend http://127.0.0.1:4000; API http://127.0.0.1:6000' \
  'Foreground processes; Ctrl-C stops only this launch. No schema/seed/file migration.'
set +e
wait -n "${children[@]}"
status=$?
set -e
exit "$status"
