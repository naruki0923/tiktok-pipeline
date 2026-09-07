"""設定・パス一元管理。

.env から機密（LINEのトークン等）を読み込み、プロジェクト内の各スクリプトの
場所を絶対パスで解決する。他モジュールはここだけを見ればよい。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# 統括/LINEbot/ の1つ上の1つ上 = プロジェクトルート
BOT_DIR = Path(__file__).resolve().parent
ROOT = BOT_DIR.parent.parent

load_dotenv(BOT_DIR / ".env")

# --- LINE 機密 ---
CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
# 通知先（自分のユーザーID）。空なら最初にメッセージを送ってきた人を自動登録。
ALLOWED_USER_ID = os.environ.get("LINE_ALLOWED_USER_ID", "")

# --- ローカルサーバ ---
PORT = int(os.environ.get("PORT", "8000"))

# --- 各工程スクリプト ---
VIDEO_DIR = ROOT / "3_動画生成"
VIDEO_SCRIPTS = VIDEO_DIR / "scripts"
VIDEO_PY = VIDEO_SCRIPTS / ".venv" / "bin" / "python"
SCRIPT_TXT_DIR = VIDEO_DIR / "音声"          # 台本テキストの置き場（本番_XXX.txt）
OUTPUT_DIR = VIDEO_DIR / "output"            # 完成動画の出力先

POST_SCRIPTS = ROOT / "4_投稿" / "scripts"
POST_PY = POST_SCRIPTS / ".venv" / "bin" / "python"
CAPTION_DIR = ROOT / "4_投稿" / "投稿予定"    # キャプション（本番_XXX.txt）

# プレビュー用に生成する一時ポスター画像の置き場
MEDIA_CACHE = BOT_DIR / ".cache"
MEDIA_CACHE.mkdir(exist_ok=True)

LINE_API = "https://api.line.me/v2/bot"


def missing_secrets() -> list[str]:
    """未設定の必須機密を返す。空リストなら準備OK。"""
    lack = []
    if not CHANNEL_SECRET:
        lack.append("LINE_CHANNEL_SECRET")
    if not CHANNEL_ACCESS_TOKEN:
        lack.append("LINE_CHANNEL_ACCESS_TOKEN")
    return lack
