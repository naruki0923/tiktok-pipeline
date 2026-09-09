#!/usr/bin/env bash
# Discord Bot を起動する。トンネル不要（botから常時接続を張るため）。
#
# 二重起動を防ぐ: 同じBotが2つ動くと、1つのメッセージに2回返事をして、
# 18:00の自動実行も同時に2本走ってしまう（実際に3つ起動して事故りかけた）。
set -euo pipefail
cd "$(dirname "$0")"

PIDFILE=".bot.pid"
# PIDが生きているだけでは足りない。exec でこのシェルごと置き換わるので trap は走らず、
# 落ちた時は死んだPIDが .bot.pid に残る。そのPIDが別のプロセスに再利用されると
# 「起動中」と誤判定して二度と上がってこない（launchd の KeepAlive が30秒ごとに
# 叩き続けるだけになる）。中身が本当にこのBotかどうかまで見る。
if [ -f "$PIDFILE" ]; then
  OLD="$(cat "$PIDFILE")"
  if kill -0 "$OLD" 2>/dev/null && ps -p "$OLD" -o command= 2>/dev/null | grep -q "bot\.py"; then
    echo "⚠ 既に起動中です (PID $OLD)。止めるなら ./stop.sh" >&2
    exit 1
  fi
fi
echo $$ > "$PIDFILE"
trap 'rm -f "$PIDFILE"' EXIT

# -u: ログをリダイレクトしても即座に出るようにする（バッファで見えなくなるのを防ぐ）
exec ./.venv/bin/python -u bot.py
