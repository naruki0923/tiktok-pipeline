#!/usr/bin/env python3
"""投稿先のTikTokアカウント名を、コードの外から読む。

⚠️ **アカウント名をコードやドキュメントに書かないこと。** このリポジトリは
   公開しているので、書くと GitHub とチャンネルが紐づく。

置き場所はプロジェクト直下の `.env`（`.gitignore` 済み）:

    TIKTOK_ACCOUNT=yourname      # @ は付けても付けなくてもよい

環境変数が先。無ければ `.env` を読む（python-dotenv に依存しないよう自前で読む。
このファイルを使う 1_リサーチ / 5_分析 には venv が無く、借りてくる venv によって
dotenv が入っていたり入っていなかったりするため）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / ".env"


def _from_env_file() -> str:
    if not ENV_FILE.exists():
        return ""
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("TIKTOK_ACCOUNT="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def tiktok_account(required: bool = True) -> str:
    """アカウント名（`@` 無し）。見つからなければ理由つきで落とす。"""
    name = (os.getenv("TIKTOK_ACCOUNT") or _from_env_file()).strip().lstrip("@")
    if not name and required:
        raise SystemExit(
            f"TIKTOK_ACCOUNT が設定されていません。\n"
            f"  {ENV_FILE} に  TIKTOK_ACCOUNT=<アカウント名>  を書いてください"
        )
    return name


def own_accounts() -> set[str]:
    """自分のチャンネル。参考動画から外すために使う。

    空のまま黙って進むと**自分の動画を参考動画に選ぶ**ので、警告は出す
    （リサーチ自体は続けられるので落とさない）。
    """
    name = tiktok_account(required=False)
    if not name:
        print(
            f"⚠️ TIKTOK_ACCOUNT が未設定です（{ENV_FILE}）。"
            "自分の動画を参考動画から除外できません。",
            file=sys.stderr,
        )
        return set()
    return {a.strip().lstrip("@") for a in name.split(",") if a.strip()}
