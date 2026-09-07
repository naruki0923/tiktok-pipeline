#!/usr/bin/env python3
"""既に開いているアップロード画面のキャプションだけ入れ直す（再アップロード不要）。

post_tiktok.py の set_caption が効かなかった時の切り分け＆修復用。
CDPで起動中Chromeに接続し、アップロードページのキャプション編集欄を探して
指定テキストを注入する。診断のため、見つけた候補と結果テキストを表示する。

使い方:
  ./.venv/bin/python -u inject_caption.py --caption-file ../投稿予定/本番_015.txt
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

import browser_ctx

CAPTION_SELECTORS = [
    "div[contenteditable='true'][role='combobox']",
    "div.public-DraftEditor-content",
    "div.notranslate.public-DraftEditor-content",
    "div[data-e2e='upload-caption'] div[contenteditable='true']",
    "div[contenteditable='true']",
]


def find_upload_page(context):
    for pg in context.pages:
        if "upload" in (pg.url or ""):
            return pg
    return context.pages[0] if context.pages else None


def inject(page, caption: str) -> bool:
    # 診断: contenteditable要素を列挙
    handles = page.query_selector_all("div[contenteditable='true']")
    print(f"[診断] contenteditable要素: {len(handles)}個", flush=True)
    for i, h in enumerate(handles):
        try:
            txt = h.inner_text()[:40].replace("\n", "⏎")
            print(f"  [{i}] text='{txt}'", flush=True)
        except Exception:
            pass

    el = None
    for sel in CAPTION_SELECTORS:
        try:
            el = page.wait_for_selector(sel, timeout=4000, state="visible")
            if el:
                print(f"[採用] セレクタ: {sel}", flush=True)
                break
        except Exception:
            continue
    if not el:
        print("❌ キャプション編集欄が見つからない", flush=True)
        return False

    el.scroll_into_view_if_needed()
    el.click()
    page.wait_for_timeout(400)
    # 全選択して削除（既存のファイル名等を消す）
    page.keyboard.press("Meta+A")
    page.keyboard.press("Delete")
    page.wait_for_timeout(300)

    # 行ごとに入力。行間はEnter（TikTokキャプションは改行可）。
    for li, line in enumerate(caption.split("\n")):
        if li > 0:
            page.keyboard.press("Enter")
            page.wait_for_timeout(150)
        for token in line.split(" "):
            if not token:
                continue
            page.keyboard.type(token, delay=25)
            page.keyboard.type(" ")
            if token.startswith("#"):
                # ハッシュタグ候補ポップアップを閉じる
                page.wait_for_timeout(600)
                page.keyboard.press("Escape")
    page.wait_for_timeout(600)

    # 結果確認
    result = el.inner_text().strip()
    print(f"[結果] 入力後テキスト:\n---\n{result}\n---", flush=True)
    return len(result) > 0


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--caption", help="キャプション本文")
    g.add_argument("--caption-file", help="キャプションtxt")
    args = ap.parse_args()

    caption = (Path(args.caption_file).read_text(encoding="utf-8").strip()
               if args.caption_file else args.caption)

    browser_ctx.start_chrome()
    with sync_playwright() as p:
        browser, context, _ = browser_ctx.connect(p)
        page = find_upload_page(context)
        if not page:
            print("❌ アップロードページが見つからない。post_tiktok.pyで先に開いてください。", flush=True)
            return 1
        print(f"[対象ページ] {page.url}", flush=True)
        ok = inject(page, caption)
        # browser.close() は実Chromeまで閉じるため呼ばない。
        # with sync_playwright() の終了でCDP接続だけを破棄する。
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
