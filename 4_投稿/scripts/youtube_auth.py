#!/usr/bin/env python3
"""YouTube Data API v3 の認証（初回のみ / トークン更新）

パスワードは扱わない。Google公式のOAuth 2.0（InstalledAppFlow）で、ブラウザが
開いて社長がGoogleアカウントで許可 → その場でトークンが youtube_token.json に
保存される。以後 youtube_upload.py はこのトークンを自動リフレッシュして再利用する
（TikTokの login.py と同じ「初回だけ手動、あとは無人」の思想）。

【事前準備（社長が一度だけ・Google Cloud Console）】
  1. https://console.cloud.google.com/ でプロジェクトを作成（任意の名前）
  2. 「APIとサービス」→「ライブラリ」→「YouTube Data API v3」を有効化
  3. 「OAuth同意画面」: User Type=外部 / アプリ名など入力 / 対象ユーザー(テスト)に
     投稿に使うGoogleアカウント(YouTubeチャンネルを管理するアカウント)を追加
  4. 「認証情報」→「認証情報を作成」→「OAuthクライアントID」→ 種類=デスクトップアプリ
  5. 作成後 JSON をダウンロードし、このフォルダに client_secret.json という名前で置く
     （youtube_upload.py と同じ 4_投稿/scripts/ 直下）

使い方:
  ./.venv/bin/python youtube_auth.py          # ブラウザが開く→許可→token保存
  ./.venv/bin/python youtube_auth.py --check   # 現在のトークンでチャンネル名を表示（疎通確認）

置き場所:
  client_secret.json  … 社長がDLして配置（機密・gitに入れない）
  youtube_token.json  … このスクリプトが生成（機密・gitに入れない）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCRIPT_DIR = Path(__file__).parent
CLIENT_SECRET = SCRIPT_DIR / "client_secret.json"
TOKEN_FILE = SCRIPT_DIR / "youtube_token.json"

# 動画のアップロード権限。youtube.upload はアップロードに必要な最小スコープ。
# youtube.force-ssl は既存動画のタイトル/説明等の更新(videos.update)に必要
# （youtube.uploadだけだとinsertはできてもupdateは権限不足(403)になる。2026-07-24判明）。
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly",
          "https://www.googleapis.com/auth/youtube.force-ssl"]


def get_credentials(interactive: bool = True) -> Credentials | None:
    """有効な認証情報を返す。無ければ（interactive=Trueなら）ブラウザでOAuthを実行。

    - token があればロードし、期限切れならリフレッシュ。
    - token が無い/リフレッシュ不可なら client_secret.json からOAuthフローを起動。
    interactive=False のときは、その場で認証フローは開かず None を返す
    （youtube_upload.py の無人実行で「先に youtube_auth.py を」と促すため）。
    """
    creds: Credentials | None = None
    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        except Exception:
            creds = None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
            return creds
        except Exception as e:
            print(f"⚠️ トークンのリフレッシュに失敗: {e}")
            creds = None

    if not interactive:
        return None

    if not CLIENT_SECRET.exists():
        print(f"❌ {CLIENT_SECRET.name} がありません。")
        print("   Google Cloud で OAuthクライアント(デスクトップアプリ)を作成し、")
        print(f"   JSONを {CLIENT_SECRET} に置いてください（このファイル冒頭の手順参照）。")
        return None

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
    # ローカルにサーバを立て、ブラウザで許可 → コールバックでトークン取得。
    creds = flow.run_local_server(port=0, prompt="consent")
    TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    print(f"✅ 認証成功。トークンを保存しました: {TOKEN_FILE.name}")
    return creds


def _check(creds: Credentials) -> int:
    """疎通確認: 認証済みチャンネルの名前とIDを表示。"""
    yt = build("youtube", "v3", credentials=creds)
    resp = yt.channels().list(part="snippet", mine=True).execute()
    items = resp.get("items", [])
    if not items:
        print("⚠️ このアカウントに紐づくYouTubeチャンネルが見つかりません。")
        print("   投稿に使うチャンネルを持つGoogleアカウントで認証し直してください。")
        return 1
    ch = items[0]
    print(f"✅ 認証OK: チャンネル「{ch['snippet']['title']}」 (id={ch['id']})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="YouTube Data API 認証")
    ap.add_argument("--check", action="store_true",
                    help="現在のトークンでチャンネル名を表示（疎通確認）")
    args = ap.parse_args()

    creds = get_credentials(interactive=not args.check or True)
    if not creds:
        return 1
    if args.check:
        return _check(creds)
    # 通常実行でも軽く疎通確認して安心材料にする
    try:
        return _check(creds)
    except Exception:
        print("✅ トークンは取得済み（チャンネル確認はスキップ）。")
        return 0


if __name__ == "__main__":
    sys.exit(main())
