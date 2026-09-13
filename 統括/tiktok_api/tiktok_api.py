#!/usr/bin/env python3
"""TikTok Display API（公式）で自分の全動画の再生数を取る。標準ライブラリのみ。

Studio の CSV エクスポートは「期間内の上位15本」しか出ない（issue #16）。
公式 API の `video.list` なら自分の全動画が取れるので、これを 5_分析/取込/ に
Content.csv と同じ列で書き出し、既存の合算（update_results.py）にそのまま乗せる。

準備（1回だけ）:
  1. developers.tiktok.com のアプリ「Video Stats Sync」の Client key / secret を
     プロジェクト直下の .env に書く（Sandbox で試す間は Sandbox の値）:
        TIKTOK_CLIENT_KEY=...
        TIKTOK_CLIENT_SECRET=...
  2. 認可（運用アカウントでログインして許可する。トークンは tiktok_token.json に保存）:
        python3 tiktok_api.py auth
  3. 取得（以後はこれだけ。トークンは自動で更新）:
        python3 tiktok_api.py fetch            # 取込/Content_api_YYYYMMDD_HHMMSS.csv を書く

認可の流れ: ブラウザで https://naruki0923.github.io/tiktok-pipeline/ を開く →
「Log in with TikTok」→ TikTok で許可 → callback.html に code が出る → ここに貼る。
（TikTok は localhost へのリダイレクトを許さないので、公開ページに code を表示させて手で渡す）
"""
from __future__ import annotations

import csv
import json
import os
import secrets
import sys
import time
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ENV_FILE = ROOT / ".env"
TOKEN_FILE = HERE / "tiktok_token.json"          # *_token.json は .gitignore 済み
INBOX = ROOT / "5_分析" / "取込"

SITE = "https://naruki0923.github.io/tiktok-pipeline/"
REDIRECT_URI = SITE + "callback.html"
SCOPES = "user.info.basic,video.list"
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
USER_URL = "https://open.tiktokapis.com/v2/user/info/?fields=display_name,avatar_url,open_id"
LIST_URL = ("https://open.tiktokapis.com/v2/video/list/?fields="
            "id,title,share_url,create_time,view_count,like_count,comment_count,share_count")


# --- 設定 -------------------------------------------------------------------
def _env(name: str) -> str:
    v = os.getenv(name, "")
    if v:
        return v.strip()
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def creds() -> tuple[str, str]:
    key, sec = _env("TIKTOK_CLIENT_KEY"), _env("TIKTOK_CLIENT_SECRET")
    if not key or not sec:
        sys.exit(f"TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET を {ENV_FILE} に書いてください")
    return key, sec


# --- HTTP -------------------------------------------------------------------
def _post_form(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _api(url: str, token: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        res = json.load(r)
    err = res.get("error", {})
    if err.get("code") not in (None, "ok"):
        raise RuntimeError(f"{err.get('code')}: {err.get('message')}")
    return res.get("data", {})


# --- トークン ---------------------------------------------------------------
def save_token(tok: dict) -> None:
    tok["obtained_at"] = int(time.time())
    TOKEN_FILE.write_text(json.dumps(tok, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(TOKEN_FILE, 0o600)


def load_token() -> dict:
    if not TOKEN_FILE.exists():
        sys.exit(f"トークンがありません。先に `python3 {Path(__file__).name} auth` を実行してください")
    tok = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    if time.time() > tok["obtained_at"] + tok.get("expires_in", 86400) - 300:
        key, sec = creds()
        new = _post_form(TOKEN_URL, {"client_key": key, "client_secret": sec,
                                     "grant_type": "refresh_token",
                                     "refresh_token": tok["refresh_token"]})
        if "access_token" not in new:
            sys.exit(f"トークン更新に失敗: {new}")
        save_token(new)
        tok = new
    return tok


def auth() -> None:
    key, sec = creds()
    state = secrets.token_urlsafe(12)
    q = urllib.parse.urlencode({"client_key": key, "scope": SCOPES, "response_type": "code",
                                "redirect_uri": REDIRECT_URI, "state": state})
    # 自前のサイトから TikTok へ飛ぶ（審査用のデモ動画もこの画面を録る）
    page = SITE + "?" + urllib.parse.urlencode({"k": key, "state": state})
    print("ブラウザで開きます:", page)
    print("（開かなければ直接 TikTok の許可画面へ）:", AUTH_URL + "?" + q)
    webbrowser.open(page)
    code = input("\ncallback.html に表示された code を貼り付けて Enter: ").strip()
    if not code:
        sys.exit("code が空です")
    tok = _post_form(TOKEN_URL, {"client_key": key, "client_secret": sec, "code": code,
                                 "grant_type": "authorization_code",
                                 "redirect_uri": REDIRECT_URI})
    if "access_token" not in tok:
        sys.exit(f"トークン取得に失敗: {tok}")
    save_token(tok)
    me = _api(USER_URL, tok["access_token"]).get("user", {})
    print(f"✔ 接続しました: {me.get('display_name')}  → {TOKEN_FILE}")


# --- 取得 -------------------------------------------------------------------
def fetch_all(token: str) -> list[dict]:
    videos, cursor, has_more = [], 0, True
    while has_more:
        d = _api(LIST_URL, token, {"max_count": 20, "cursor": cursor})
        videos += d.get("videos", [])
        cursor, has_more = d.get("cursor", 0), d.get("has_more", False)
    return videos


def write_csv(videos: list[dict]) -> Path:
    """Studio の Content.csv と同じ列で書く（update_results.py がそのまま読める）。"""
    INBOX.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    path = INBOX / f"Content_api_{now:%Y%m%d_%H%M%S}.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL)
        w.writerow(["Time", "Video title", "Video link", "Post time",
                    "Total likes", "Total comments", "Total shares", "Total views"])
        for v in videos:
            posted = datetime.fromtimestamp(int(v.get("create_time", 0)))
            w.writerow([f"{now.month}月{now.day}日", v.get("title", ""), v.get("share_url", ""),
                        f"{posted.year}-{posted.month:02d}-{posted.day:02d}",
                        v.get("like_count", 0), v.get("comment_count", 0),
                        v.get("share_count", 0), v.get("view_count", 0)])
    return path


def fetch() -> None:
    tok = load_token()
    me = _api(USER_URL, tok["access_token"]).get("user", {})
    print(f"アカウント: {me.get('display_name')}")
    videos = fetch_all(tok["access_token"])
    videos.sort(key=lambda v: int(v.get("create_time", 0)), reverse=True)
    print(f"{'公開日':10} {'再生数':>9}  タイトル")
    for v in videos:
        d = datetime.fromtimestamp(int(v.get("create_time", 0)))
        print(f"{d:%Y-%m-%d} {int(v.get('view_count', 0)):>9,}  {v.get('title', '')[:40]}")
    path = write_csv(videos)
    print(f"\n✔ {len(videos)}本 → {path}")
    print("次: cd 5_分析/scripts && python3 update_results.py   （取込/ を合算して 実績.tsv へ）")


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "auth":
        auth()
    elif cmd == "fetch":
        fetch()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
