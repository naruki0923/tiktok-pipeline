#!/usr/bin/env python3
"""TikTokログイン → セッション保存（自動化フラグなしの普通のChrome）

パスワードはこのスクリプトでは一切扱わない。自動化フラグを付けずに普通の
Google Chromeを専用プロファイルで起動するので、TikTokから見て「ユーザーが
手で開いたChrome」と区別がつかず、通常どおりログインできる（QR/メール/電話どれでも）。
Playwrightは起動には関与せず、ログイン完了（sessionid Cookie）の検知のためだけに
デバッグポート経由で接続する。ターミナル操作は不要。

ログイン完了後はセッションが専用プロファイル(browser_ctx.PROFILE_DIR)に残り、
post_tiktok.py が同じプロファイルを再利用する。

使い方:
  ./.venv/bin/python login.py            # 最大5分ログインを待つ
  ./.venv/bin/python login.py --wait 600 # 待ち時間(秒)を変更
"""
from __future__ import annotations

import argparse
import sys
import time

from playwright.sync_api import sync_playwright

import browser_ctx

LOGIN_URL = "https://www.tiktok.com/login"


def _is_logged_in(context) -> bool:
    """ログイン済みかを sessionid Cookie の有無で判定。"""
    try:
        cookies = context.cookies()
        return any(c.get("name") == "sessionid" and c.get("value") for c in cookies)
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="TikTokログイン→セッション保存")
    ap.add_argument("--wait", type=int, default=300, help="ログイン完了を待つ最大秒数（既定300）")
    args = ap.parse_args()

    print("普通のGoogle Chromeを開きます（自動化フラグなし）。")
    print("表示された画面で、いつも通りログインしてください（投稿用アカウント）。")
    print(f"ログインを最大 {args.wait} 秒待ちます。完了を検知したら自動で保存します。")

    proc = browser_ctx.start_chrome()
    try:
        with sync_playwright() as p:
            browser, context, page = browser_ctx.connect(p)

            if _is_logged_in(context):
                print("✅ 既にログイン済みです（このプロファイルにセッションあり）。")
                return 0

            try:
                page.goto(LOGIN_URL, wait_until="domcontentloaded")
            except Exception:
                pass  # ユーザーが自分でURLを開いてもよい

            deadline = time.time() + args.wait
            detected = False
            while time.time() < deadline:
                if _is_logged_in(context):
                    detected = True
                    break
                time.sleep(3)

            if not detected:
                print("⚠️  時間内にログインを検知できませんでした。もう一度実行してください。")
                print("    （画面にエラー/CAPTCHAが出ている場合は、その内容を教えてください）")
                return 1

            print(f"✅ ログインを検知。セッションを保存しました（プロファイル: {browser_ctx.PROFILE_DIR.name}）。")
            print("   このChrome窓は閉じて構いません。")
            return 0
    finally:
        # Chromeプロセスは開いたままにする（ユーザーが窓を閉じられるように）。
        # プロファイルにセッションは保存済みなので、閉じても再利用できる。
        pass


if __name__ == "__main__":
    sys.exit(main())
