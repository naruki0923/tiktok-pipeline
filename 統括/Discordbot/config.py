"""設定・パス一元管理（Discord bot 版）。

.env から機密を読み、各工程スクリプトの場所を絶対パスで解決する。
他モジュールはここだけを見ればよい（LINEbot/config.py の後継）。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BOT_DIR = Path(__file__).resolve().parent          # 統括/Discordbot
ROOT = BOT_DIR.parent.parent                        # プロジェクトルート

load_dotenv(BOT_DIR / ".env")

# --- Discord 機密 ---
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
# 通知・操作を受け付けるチャンネルID（数字）。空なら最初に話しかけられた場所を採用。
CHANNEL_ID = int(os.environ.get("DISCORD_CHANNEL_ID") or 0)
# 任意: このユーザーID以外のコマンドを無視する
ALLOWED_USER_ID = int(os.environ.get("DISCORD_ALLOWED_USER_ID") or 0)
# 動画が出来て「あとは投稿だけ」の時に名指し(@)して通知を飛ばす相手。
# 未設定なら ALLOWED_USER_ID → 最初に話しかけてきた人（state.json）の順に使う。
MENTION_USER_ID = int(os.environ.get("DISCORD_MENTION_USER_ID") or 0) or ALLOWED_USER_ID

# --- 自動実行 ---
AUTO_TIME = os.environ.get("AUTO_TIME", "18:00")            # 毎日この時刻に走る
AUTO_ENABLED = os.environ.get("AUTO_ENABLED", "1") == "1"   # 起動時の既定

# --- 週次レビュー（分析 → 検索語・台本方針へ自動反映）---
# 毎週この曜日・時刻に 5_分析/scripts/weekly_review.py を回す。曜日は 0=月 … 6=日。
# 生成（毎日06:00）と違い**作るのは提案とレポートだけ**で、投稿には一切触らない。
WEEKLY_ENABLED = os.environ.get("WEEKLY_ENABLED", "1") == "1"
WEEKLY_DOW = int(os.environ.get("WEEKLY_DOW", "6"))          # 既定=日曜
WEEKLY_TIME = os.environ.get("WEEKLY_TIME", "22:00")

# --- エラー時のCodex救援 ---
# APIは使わず、このMacでChatGPTログイン済みの codex CLIだけを使う。
AUTO_CODEX_RESCUE = os.environ.get("AUTO_CODEX_RESCUE", "1") == "1"
CODEX_RESCUE_TIMEOUT = int(os.environ.get("CODEX_RESCUE_TIMEOUT", "1800"))

# --- エラー後の自動作り直し（2026-08-17 社長判断で自動化）---
# 生成でコケたら Codex に直させ、そのまま自動でもう一度作る。何回まで作り直すか。
# 0 にすると従来どおり「社長が『作って』と言い直す」運用に戻る。
# **投稿は対象外**（TikTok/YouTubeへ出すのは今までどおり社長の合図が要る）。
AUTO_RETRY_MAX = int(os.environ.get("AUTO_RETRY_MAX", "2"))
# Codexがコードを直さなかった時（TikTok側の一時制限など）に空ける時間（秒）。
# 直った時は待たずにすぐ作り直す。
AUTO_RETRY_WAIT = int(os.environ.get("AUTO_RETRY_WAIT", "300"))

# --- 各工程スクリプト ---
RESEARCH_SCRIPTS = ROOT / "1_リサーチ" / "scripts"
AUTO_RESEARCH = RESEARCH_SCRIPTS / "auto_research.py"

GEN_SCRIPTS = ROOT / "2_台本生成" / "scripts"
GEN_PY = GEN_SCRIPTS / ".venv" / "bin" / "python"      # fugashi・yt-dlp・whisper入り
USE_REF = GEN_SCRIPTS / "use_ref.py"
WRITE_SCRIPT = GEN_SCRIPTS / "write_script.py"
REVISE_SCRIPT = GEN_SCRIPTS / "revise_script.py"
TRANSCRIPT_DIR = ROOT / "2_台本生成" / "文字起こし"

VIDEO_DIR = ROOT / "3_動画生成"
VIDEO_SCRIPTS = VIDEO_DIR / "scripts"
VIDEO_PY = VIDEO_SCRIPTS / ".venv" / "bin" / "python"
SCRIPT_TXT_DIR = VIDEO_DIR / "音声"                     # 台本テキスト（本番_NNN.txt）
OUTPUT_DIR = VIDEO_DIR / "output"                       # 完成動画

POST_SCRIPTS = ROOT / "4_投稿" / "scripts"
POST_PY = POST_SCRIPTS / ".venv" / "bin" / "python"     # playwright入り
CAPTION_DIR = ROOT / "4_投稿" / "投稿予定"
POST_LOG = ROOT / "4_投稿" / "ログ" / "post_log.csv"    # 5_分析 がキャプションで回を突合する

ANALYSIS_SCRIPTS = ROOT / "5_分析" / "scripts"
WEEKLY_REVIEW = ANALYSIS_SCRIPTS / "weekly_review.py"   # 週次レビュー（標準ライブラリのみ）

# プレビュー用の圧縮動画・ポスターの置き場
CACHE = BOT_DIR / ".cache"
CACHE.mkdir(exist_ok=True)
STATE = BOT_DIR / "state.json"                          # 自動実行の状態・title_lines等

# Discordの添付上限に収める目標サイズ（無料枠10MBに対して余裕を持たせる）
PREVIEW_MAX_MB = 8.0


def missing_secrets() -> list[str]:
    """未設定の必須機密を返す。空リストなら準備OK。"""
    return [] if BOT_TOKEN else ["DISCORD_BOT_TOKEN"]
