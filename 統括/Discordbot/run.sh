#!/usr/bin/env bash
# Discord Bot を起動する。トンネル不要（botから常時接続を張るため）。
#
# 二重起動を防ぐ: 同じBotが2つ動くと、1つのメッセージに2回返事をして、
# 18:00の自動実行も同時に2本走ってしまう（実際に3つ起動して事故りかけた）。
set -euo pipefail
cd "$(dirname "$0")"

PIDFILE=".bot.pid"
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "⚠ 既に起動中です (PID $(cat "$PIDFILE"))。止めるなら ./stop.sh" >&2
  exit 1
fi
echo $$ > "$PIDFILE"
trap 'rm -f "$PIDFILE"' EXIT

# -u: ログをリダイレクトしても即座に出るようにする（バッファで見えなくなるのを防ぐ）
exec ./.venv/bin/python -u bot.py
