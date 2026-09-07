#!/usr/bin/env python3
"""5_分析: TikTokインサイトを自動取得して metrics.csv に追記する（Checkの無人化）。

4_投稿 の browser_ctx（自動化フラグなしChrome＋CDP後接続でTikTok検知回避）を再利用する。
ログインセッションは 4_投稿/scripts/.chrome-profile を共有（先に login.py 済みが前提）。

段階運用:
  # ① まず実物のDOMを見る（selector設計・デバッグ用）。数値は書き込まない。
  ./metrics_fetch.py --inspect
  # ② 取得して metrics.csv に追記（本番）
  ./metrics_fetch.py --video 本番_015_TikTok

依存: 4_投稿/scripts/.venv の playwright を使うため、そのvenvで実行すること:
  ../../4_投稿/scripts/.venv/bin/python metrics_fetch.py --inspect
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
POST_SCRIPTS = BASE / "4_投稿" / "scripts"
sys.path.insert(0, str(POST_SCRIPTS))        # browser_ctx を借りる
sys.path.insert(0, str(Path(__file__).resolve().parent))  # record を借りる

import browser_ctx  # noqa: E402
import record  # noqa: E402  (FIELDS / probe_duration / lookup_post_datetime を再利用)
from playwright.sync_api import sync_playwright  # noqa: E402

STUDIO_ANALYTICS = "https://www.tiktok.com/tiktokstudio/analytics"
STUDIO_CONTENT = "https://www.tiktok.com/tiktokstudio/content"
DEBUG_DIR = Path(__file__).resolve().parent.parent / "レポート" / "_fetch_debug"
METRICS = Path(__file__).resolve().parent.parent / "metrics.csv"
POST_LOG = BASE / "4_投稿" / "ログ" / "post_log.csv"

# 各動画行を「/video/リンクを1本だけ含む最大の祖先」として取り出すJS。
# TikTokのclass名はハッシュで不安定なので使わず、リンク数で行を特定する。
ROW_JS = """els => els.map(a=>{
    let row=a;
    while(row.parentElement && row.parentElement.querySelectorAll("a[href*='/video/']").length===1){
      row=row.parentElement;
    }
    return {href:a.href, text:row.innerText};
})"""


def to_int(s: str):
    """'1.1M' '11K' '1,075' '88' → int。数値でなければ None。"""
    s = s.replace(",", "").strip()
    m = re.match(r"^([\d.]+)([KM]?)$", s)
    if not m:
        return None
    return int(float(m.group(1)) * {"": 1, "K": 1e3, "M": 1e6}[m.group(2)])


def parse_rows(rows: list[dict]) -> list[dict]:
    """各行のinnerTextから 尺/キャプション/視聴数/いいね/コメント/動画ID を抽出。"""
    out = []
    for r in rows:
        lines = [l.strip() for l in r["text"].split("\n") if l.strip()]
        if len(lines) < 3:
            continue
        nums = [to_int(l) for l in lines if to_int(l) is not None]
        views, likes, comments = (nums[-3:] + [None, None, None])[:3]
        out.append({
            "尺表示": lines[0],
            "キャプション": lines[1],
            "再生数": views, "いいね": likes, "コメント": comments,
            "video_id": r["href"].split("/video/")[-1],
            "url": r["href"],
        })
    return out


def norm(s: str) -> str:
    """突合用に空白と記号ゆらぎを除去。"""
    return re.sub(r"\s+", "", s or "")


def caption_for(video_name: str) -> str:
    """post_log から動画のキャプション（最新行）を拾う。"""
    stem = video_name.rsplit(".mp4", 1)[0]
    if not POST_LOG.exists():
        return ""
    cap = ""
    with POST_LOG.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            v = (row.get("video") or "").rsplit(".mp4", 1)[0]
            if v == stem and row.get("caption"):
                cap = row["caption"]
    return cap


def inspect(page) -> None:
    """分析ページを開いて、DOM設計のためにスクショと可視テキストを保存する。"""
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    for name, url in (("analytics", STUDIO_ANALYTICS), ("content", STUDIO_CONTENT)):
        print(f"→ {url}")
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)  # データ描画待ち
        if "login" in page.url:
            print("❌ ログイン画面に飛ばされた。4_投稿/scripts/login.py を再実行してセッション更新を。")
            return
        shot = DEBUG_DIR / f"{ts}_{name}.png"
        page.screenshot(path=str(shot), full_page=True)
        txt = DEBUG_DIR / f"{ts}_{name}.txt"
        txt.write_text(page.inner_text("body"), encoding="utf-8")
        print(f"   保存: {shot.name} / {txt.name}")
    print(f"\n✓ inspect完了 → {DEBUG_DIR}\n  実物を見てから抽出selectorを確定する。")


def _real_click(page, handle) -> bool:
    """要素のbounding boxへ実マウス移動→クリック（SPAはtrustedイベントを要求するため）。"""
    el = handle.as_element() if handle else None
    box = el.bounding_box() if el else None
    if not box:
        return False
    el.scroll_into_view_if_needed()
    box = el.bounding_box() or box
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(cx, cy)
    page.wait_for_timeout(500)
    page.mouse.click(cx, cy)
    return True


def deep_inspect(page, keyword: str) -> None:
    """analytics→コンテンツタブで keyword を含む動画の「データを表示」を実クリックし、
    動画別詳細（平均視聴/維持/トラフィック/視聴者）の実DOMをキャプチャする。

    ※深い指標のパーサーを確定するための調査モード。前提: その動画が
      「過去365日以内」かつ analyticsコンテンツタブのリストに載っていること。
    """
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    page.goto(STUDIO_ANALYTICS, wait_until="domcontentloaded")
    page.wait_for_timeout(3500)
    if "login" in page.url:
        print("❌ ログイン画面。login.py を再実行。")
        return
    page.get_by_text("コンテンツ", exact=True).first.click()
    page.wait_for_timeout(3000)

    # keyword を含む行が出るまでスクロール
    found = False
    for _ in range(15):
        if keyword in page.inner_text("body"):
            found = True
            break
        page.mouse.wheel(0, 3000)
        page.wait_for_timeout(1000)
    if not found:
        print(f"❌ analyticsコンテンツタブに『{keyword}』が見つからず。")
        print("   低再生でリスト圏外か、365日超で対象外の可能性。上位リスト表示のため直近の低再生動画は載らないことがある。")
        return

    # keyword を含む行の「データを表示」ボタンを取得して実クリック
    btn = page.evaluate_handle("""(kw)=>{
      const btns=[...document.querySelectorAll('button')].filter(b=>b.innerText.includes('データを表示'));
      for(const b of btns){
        let row=b; for(let i=0;i<12;i++){ if(row.parentElement){row=row.parentElement;} if(row.innerText&&row.innerText.includes(kw))break; }
        if(row.innerText&&row.innerText.includes(kw)) return b;
      }
      return null;
    }""", keyword)
    if not _real_click(page, btn):
        print("❌ 「データを表示」ボタンをクリックできず。")
        return
    page.wait_for_timeout(5000)

    body = page.inner_text("body")
    if "365日" in body and "平均" not in body:
        print("⚠ 『過去365日間の投稿だけ』の制限メッセージ。この動画は範囲外で詳細が開けない。")
    shot = DEBUG_DIR / f"{ts}_detail.png"
    page.screenshot(path=str(shot), full_page=True)
    txt = DEBUG_DIR / f"{ts}_detail.txt"
    txt.write_text(f"URL: {page.url}\n\n{body}", encoding="utf-8")
    kws = [k for k in ["平均視聴", "視聴時間", "フル視聴", "維持", "継続",
                       "トラフィック", "おすすめ", "視聴者"] if k in body]
    print(f"✓ 詳細キャプチャ → {shot.name} / {txt.name}")
    print(f"  URL: {page.url}")
    print(f"  検出キーワード: {kws or '（深い指標なし＝範囲外/データ不足の可能性）'}")


def scrape_content(page, want_caption: str = "") -> list[dict]:
    """コンテンツ一覧を（必要なら下スクロールで追加ロードして）行抽出。"""
    page.goto(STUDIO_CONTENT, wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    if "login" in page.url:
        raise RuntimeError("ログイン画面に飛ばされた。login.py を再実行してセッション更新を。")

    seen = 0
    for _ in range(12):  # 遅延ロード対策に下までスクロール
        rows = parse_rows(page.eval_on_selector_all("a[href*='/video/']", ROW_JS))
        if want_caption and any(norm(want_caption).startswith(norm(r["キャプション"]))
                                or norm(r["キャプション"]).startswith(norm(want_caption))
                                for r in rows):
            return rows  # 目的の1本が見えたら十分
        if len(rows) == seen:
            break
        seen = len(rows)
        page.mouse.wheel(0, 4000)
        page.wait_for_timeout(1500)
    return parse_rows(page.eval_on_selector_all("a[href*='/video/']", ROW_JS))


def match_row(rows: list[dict], caption: str) -> dict | None:
    if not caption:
        return None
    nc = norm(caption)
    # 完全前方一致 → 部分一致の順で最良を選ぶ
    for r in rows:
        rc = norm(r["キャプション"])
        if rc and (nc.startswith(rc) or rc.startswith(nc)):
            return r
    return None


def append_row(video_name: str, hit: dict) -> None:
    """突合できた1本を metrics.csv に追記（自動で取れる項目のみ。深い指標は空欄）。"""
    duration = record.probe_duration(video_name)
    posted = record.lookup_post_datetime(video_name)
    row = {k: "" for k in record.FIELDS}
    row.update({
        "動画名": video_name,
        "投稿日時": posted,
        "計測日時": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "尺秒": duration or "",
        "再生数": hit["再生数"] if hit["再生数"] is not None else "",
        "いいね": hit["いいね"] if hit["いいね"] is not None else "",
        "コメント": hit["コメント"] if hit["コメント"] is not None else "",
        "メモ": f"auto:content一覧 {hit['url']}",
    })
    new_file = not METRICS.exists()
    with METRICS.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=record.FIELDS)
        if new_file:
            w.writeheader()
        w.writerow(row)


def do_fetch(args) -> int:
    if not browser_ctx.has_session():
        print(f"❌ セッション未保存: {browser_ctx.PROFILE_DIR}")
        print("   先に 4_投稿/scripts/login.py を実行してください。")
        return 1

    browser_ctx.start_chrome()
    with sync_playwright() as p:
        browser, context, page = browser_ctx.connect(p)
        try:
            if args.inspect:
                page.goto(STUDIO_ANALYTICS, wait_until="domcontentloaded")
                page.wait_for_timeout(2000)
                if "login" in page.url:
                    print("❌ ログイン画面に飛ばされた。login.py を再実行。")
                    return 1
                inspect(page)
                return 0

            if args.deep_inspect:
                deep_inspect(page, args.deep_inspect)
                return 0

            if not args.video:
                print("--video <動画名> か --inspect を指定してください。")
                return 2

            caption = caption_for(args.video)
            if not caption:
                print(f"⚠ post_log に {args.video} のキャプションが見つからず、突合できません。")
                return 2
            print(f"突合キャプション: {caption[:40]}…")

            rows = scrape_content(page, want_caption=caption)
            hit = match_row(rows, caption)
            if not hit:
                print(f"❌ 一覧({len(rows)}本)から一致する動画が見つかりませんでした。")
                print("   投稿直後で未反映か、キャプションが編集された可能性。--inspect で確認を。")
                return 2

            print(f"✓ 一致: [{hit['尺表示']}] 再生{hit['再生数']} / いいね{hit['いいね']} "
                  f"/ コメント{hit['コメント']}")
            append_row(args.video, hit)
            print(f"✓ metrics.csv に追記しました → {METRICS}")
            print("  ※平均視聴秒・継続率・おすすめ%・視聴者属性は自動未取得。"
                  "必要なら record.py で追記（TikTokアプリの他のインサイトから）。")
            return 0
        finally:
            # connect_over_cdp の browser.close() は接続解除ではなく、投稿処理も
            # 共有している実Chrome全体を閉じる。Playwright終了に接続解除を任せる。
            pass


def main() -> int:
    p = argparse.ArgumentParser(description="TikTokインサイトを自動取得してCSVへ")
    p.add_argument("--video", help="対象動画名（metrics.csv の動画名に使う）")
    p.add_argument("--inspect", action="store_true",
                   help="分析ページのスクショ/テキストを保存（selector設計用・書き込まない）")
    p.add_argument("--deep-inspect", dest="deep_inspect", metavar="KEYWORD",
                   help="動画別詳細（平均視聴/維持/トラフィック/視聴者）の実DOMを取得。"
                        "KEYWORDは動画を特定するキャプション中の語（例: 失業手当）")
    args = p.parse_args()
    return do_fetch(args)


if __name__ == "__main__":
    sys.exit(main())
