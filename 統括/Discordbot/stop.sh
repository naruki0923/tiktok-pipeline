#!/usr/bin/env bash
# Botを止める。取りこぼしがないよう、pidファイルと実プロセスの両方を見る。
set -uo pipefail
cd "$(dirname "$0")"

if [ -f .bot.pid ]; then
  kill "$(cat .bot.pid)" 2>/dev/null || true
  rm -f .bot.pid
fi
# pidファイルが無い/古い場合の保険（このディレクトリの bot.py だけを狙う）
pkill -f "$(pwd)/.venv/bin/python -u bot.py" 2>/dev/null || true
sleep 2
left=$(pgrep -f "bot\.py" | wc -l | tr -d ' ')
echo "停止しました（残り ${left} プロセス）"
