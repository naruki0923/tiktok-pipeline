#!/usr/bin/env python3
"""5_分析: 自分の投稿の「角度 × 実績（再生数）」を 5_分析/実績.tsv に書き出す。

なぜ要るか（2026-09-04）:
  台本の重複判定（write_script.py）が「この角度は前に扱ったか」しか見ておらず、
  **当たった角度ほど「重複」で弾かれて二度と作れない**状態だった。実績を持たせて
  「1万再生以上の角度は再訪してよい／むしろ優先」と判定させるための材料がこれ。

取得元:
  - 公開URL一覧 … 自分のプロフィールのグリッド（未ログインでも読める）
  - 実数        … 動画ページの埋め込みJSON（ログイン済Chromeで直叩き。Studio一覧は
                  遅延読み込みが不安定で新しい動画が落ちるため使わない）
  - 角度/タイトル … 統括/Discordbot/state.json（036以降）＋ 4_投稿/ログ/post_log.csv

使い方（4_投稿のvenvで実行。playwright が要る）:
    cd 5_分析/scripts
    ../../4_投稿/scripts/.venv/bin/python update_results.py            # 直近30本を取り直す
    ../../4_投稿/scripts/.venv/bin/python update_results.py --limit 60
    ../../4_投稿/scripts/.venv/bin/python update_results.py --from-json ../レポート/_fetch_debug/xxx.json
前提: 4_投稿/scripts/login.py 済み（.chrome-profile）。1本あたり20〜40秒かかる。
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE / "1_リサーチ" / "scripts"))
sys.path.insert(0, str(BASE / "4_投稿" / "scripts"))

from own_account import tiktok_account  # noqa: E402  (BASE を確定させてから読む)

OUT = BASE / "5_分析" / "実績.tsv"
POST_LOG = BASE / "4_投稿" / "ログ" / "post_log.csv"
STATE = BASE / "統括" / "Discordbot" / "state.json"
# ⚠️ アカウント名はコードに書かない（公開リポジトリなので）。
#    `.env` の TIKTOK_ACCOUNT から読む。詳細は 1_リサーチ/scripts/own_account.py
USER = tiktok_account()
GRID_JS = """() => [...document.querySelectorAll("a[href*='/video/']")].map(a=>a.href)"""


def _norm(s: str) -> str:
    return re.sub(r"[\s#]+", "", s or "")


def caption_index() -> dict[str, str]:
    """正規化したキャプション → 本番_NNN。"""
    idx: dict[str, str] = {}
    if not POST_LOG.exists():
        return idx
    for row in csv.reader(POST_LOG.open(encoding="utf-8")):
        if len(row) >= 5 and row[1].endswith(".mp4"):
            name = re.sub(r"_TikTok.*\.mp4$", "", row[1])
            idx.setdefault(_norm(row[4]), name)
    return idx


def name_of(caption: str, idx: dict[str, str]) -> str | None:
    c = _norm(caption)
    if c in idx:
        return idx[c]
    for known, name in idx.items():          # 末尾のタグ違いを吸収する
        if known and (known[:35] in c or c[:35] in known):
            return name
    return None


def angles() -> dict[str, dict]:
    if not STATE.exists():
        return {}
    try:
        return json.load(STATE.open(encoding="utf-8")).get("videos", {})
    except (json.JSONDecodeError, OSError):
        return {}


def collect(limit: int) -> list[dict]:
    """プロフィールから公開URLを集め、新しい順に limit 本の実数を取る。"""
    import browser_ctx
    import auto_research as ar
    from playwright.sync_api import sync_playwright

    browser_ctx.start_chrome()
    rows = []
    with sync_playwright() as p:
        _browser, _ctx, page = browser_ctx.connect(p)
        page.goto(f"https://www.tiktok.com/@{USER}", timeout=60_000,
                  wait_until="domcontentloaded")
        page.wait_for_timeout(6000)
        urls, stall = [], 0
        for _ in range(60):
            found = list(dict.fromkeys(
                u.split("?")[0] for u in page.evaluate(GRID_JS) if "/video/" in u))
            if len(found) == len(urls):
                stall += 1
                if stall >= 5:
                    break
            else:
                stall = 0
            urls = found
            page.mouse.wheel(0, 8000)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(2000)
        # 動画IDの上位32bitが投稿時刻。新しい順に並べて limit 本だけ見る。
        urls.sort(key=lambda u: int(u.rsplit("/", 1)[-1]) >> 32, reverse=True)
        urls = urls[:limit]
        print(f"公開 {len(urls)}本の実数を取ります", file=sys.stderr)
        for i, u in enumerate(urls, 1):
            data = None
            for _ in range(2):
                try:
                    data = ar.probe_page(page, u)
                except Exception as e:                       # noqa: BLE001
                    print(f"  {i}/{len(urls)} err {str(e)[:60]}", file=sys.stderr)
                if data:
                    break
                page.wait_for_timeout(4000)
            if data:
                data["url"] = u
                rows.append(data)
                print(f"  {i}/{len(urls)} {data['upload_date']} v={data['views']}",
                      file=sys.stderr, flush=True)
            else:
                print(f"  {i}/{len(urls)} ❌ {u}", file=sys.stderr, flush=True)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="角度×実績を 5_分析/実績.tsv に書き出す")
    ap.add_argument("--limit", type=int, default=30, help="取り直す本数（新しい順）")
    ap.add_argument("--from-json", type=Path, default=None,
                    help="取得済みJSON（probe_page 形式）を使う。ブラウザを開かない")
    args = ap.parse_args()

    rows = (json.loads(args.from_json.read_text(encoding="utf-8"))
            if args.from_json else collect(args.limit))
    idx, meta = caption_index(), angles()

    # 既存行は残し、今回取れたぶんだけ上書きする（古い動画の実績を消さない）
    keep: dict[str, list[str]] = {}
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            if line.startswith("#") or not line.strip():
                continue
            cols = line.split("\t")
            if cols:
                keep[cols[0]] = cols

    for r in rows:
        name = name_of(r.get("title", ""), idx)
        if not name:
            continue
        m = meta.get(name, {})
        pub = r.get("upload_date", "")
        if len(pub) == 8:
            pub = f"{pub[:4]}-{pub[4:6]}-{pub[6:]}"
        title = m.get("title") or re.sub(r"\s*#.*$", "", r.get("title", "")).strip()
        title = title.replace("【無料の個別相談はLINEから💬️】", "")
        keep[name] = [name, pub, str(r.get("views", 0)),
                      (m.get("angle") or title)[:60], title[:60]]

    lines = ["# 自分の投稿の角度×実績。write_script.py の重複判定が読む。",
             "# 動画名\t公開日\t再生数\t角度\tタイトル"]
    lines += ["\t".join(v) for _, v in sorted(keep.items())]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"✓ {len(keep)}本 → {OUT}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
