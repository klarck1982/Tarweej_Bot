#!/usr/bin/env bash
# يجهّز بيئة المحاكاة (venv + PostgreSQL 17 على المنفذ 5433). آمن للتكرار.
set -e
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
[ -x /tmp/venv/bin/python ] || { python3 -m venv /tmp/venv && /tmp/venv/bin/pip install -q -r "$ROOT/requirements.txt" pytest; }
if [ ! -d /usr/lib/postgresql ]; then
  sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql >/tmp/apt.log 2>&1
fi
P=$(ls -d /usr/lib/postgresql/*/bin | tail -1)
if ! $P/pg_isready -h 127.0.0.1 -p 5433 -q 2>/dev/null; then
  [ -d /tmp/pgdata ] || $P/initdb -D /tmp/pgdata -U postgres --auth=trust -E UTF8 >/dev/null
  $P/pg_ctl -D /tmp/pgdata -o "-p 5433 -k /tmp" -l /tmp/pg.log start >/dev/null
  sleep 2
fi
cat > /tmp/freshdb.sh <<EOS
$P/psql -q -h 127.0.0.1 -p 5433 -U postgres -c "drop database if exists promobot with (force)" -c "create database promobot"
EOS
chmod +x /tmp/freshdb.sh
echo "✅ البيئة جاهزة — /tmp/freshdb.sh لقاعدة جديدة"
