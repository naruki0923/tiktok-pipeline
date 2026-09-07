#!/usr/bin/env bash
# LINE Bot を起動する。トンネル→webhook自動登録→待受まで一括。
set -euo pipefail
cd "$(dirname "$0")"
exec ./.venv/bin/python server.py
