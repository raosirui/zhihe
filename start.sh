#!/usr/bin/env bash
# 知核 · 一键启动（脱离终端会话，nohup 保活）
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"

# MySQL
if ! mysqladmin -S /var/run/mysqld/mysqld.sock ping >/dev/null 2>&1; then
  sudo service mysql start
fi

# 后端 6000
if ! curl -s http://localhost:6000/api/health >/dev/null 2>&1; then
  cd "$ROOT/backend"
  setsid nohup .venv/bin/python app.py > /tmp/zhihe-backend.log 2>&1 < /dev/null &
  echo "backend starting... log: /tmp/zhihe-backend.log"
fi

# 前端 4000
if ! curl -s -o /dev/null http://localhost:4000/ 2>/dev/null; then
  cd "$ROOT/frontend"
  setsid nohup npm run dev > /tmp/zhihe-frontend.log 2>&1 < /dev/null &
  echo "frontend starting... log: /tmp/zhihe-frontend.log"
fi

sleep 4
echo "backend : $(curl -s -o /dev/null -w '%{http_code}' http://localhost:6000/api/health)"
echo "frontend: $(curl -s -o /dev/null -w '%{http_code}' http://localhost:4000/)"
