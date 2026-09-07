#!/usr/bin/env python3
"""1_リサーチ の無人化: 参考動画の候補をTikTokから自動収集して採点する。

これまで手（Claude＋ブラウザ操作）でやっていた「検索→数値を見る→黄金式で絞る」を
スクリプト化したもの。出力は候補リストのJSONで、2_台本生成 側がこれを上から順に
試す（文字起こし→季節性判定→台本化）。

    # 4_投稿 の venv（playwright入り）で動かす
    ../../4_投稿/scripts/.venv/bin/python auto_research.py -o ../候補_20260803.json

やること:
  ① 検索      … /search/video?q=<語> のグリッドから動画URLを集める（未ログインでも見える）
  ② 除外      … 採用履歴.tsv にある動画は30日間スキップ（再利用禁止ルール）
  ③ 実数取得  … 動画ページの埋め込みJSONから 再生数・尺・投稿日・いいね・コメント・保存・
                フォロワー を取る（**ブラウザが本線・yt-dlpは保険**。理由は probe_page のコメント）
  ④ フォロワー … ③で取れなかった時だけ /search/user?q=<アカウント> から補う
  ⑤ 採点      … 黄金式（フォロワー×100＜再生）と Eng率 で並べる

TikTok側の作りは頻繁に変わる。ここは「取れなければ静かに諦めて次へ」を徹底し、
1つのつまずきで全体が止まらないようにしてある（詰まったら候補ゼロで返す）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent          # 1_リサーチ/scripts
ROOT = HERE.parent.parent                        # プロジェクトルート
POST_SCRIPTS = ROOT / "4_投稿" / "scripts"
HISTORY = HERE.parent / "採用履歴.tsv"
YT_DLP = ROOT / "2_台本生成" / "scripts" / ".venv" / "bin" / "yt-dlp"

sys.path.insert(0, str(POST_SCRIPTS))            # browser_ctx（検知回避Chrome）を借りる

from own_account import own_accounts  # noqa: E402  (HERE を確定させてから読む)

# 既定の検索語。1語につき12件しか出ない（未ログインの上限）ので複数振って件数を稼ぐ。
# 2026-09-04 に伸びる型へ寄せた。実測（042〜066）で当たったのは
#   年齢トリガー（60歳/65歳）… 中央値13,750・最大257,200
#   取られるお金を減らす（国保/住民税）… 243,000・77,000・1.1M
# 逆に「申請ステップ・失業認定の手続き」系は n=9 で中央値2,841と沈んでいたので、
# その語（"失業手当 申請" / "ハローワーク 失業認定" / "退職 手続き 損"）を外した。
DEFAULT_KEYWORDS = [
    "退職給付金",
    "65歳 退職 失業保険",
    "60歳 給付金 申請",
    "退職後 国民健康保険 減免",
    "退職 住民税 安くする",
    "失業保険 知らないと損",
]

KEYWORDS_JSON = HERE.parent / "検索語.json"   # 週次レビューが書き換える（無ければ既定値）


def search_keywords() -> list[str]:
    """今回使う検索語。`検索語.json` があればそちらを優先する。

    どの語で探すかは実測で変わる（2026-09-04に手で入れ替えた）。それを毎週
    自動でやるのが 5_分析/scripts/weekly_review.py で、書き込み先がこのJSON。
    **コード側の DEFAULT_KEYWORDS は残す**: JSONが無い・壊れている・語が
    ゼロになった時は既定値に戻り、リサーチが止まらないようにする。
    """
    try:
        data = json.loads(KEYWORDS_JSON.read_text(encoding="utf-8"))
        kws = [str(k).strip() for k in data.get("keywords", []) if str(k).strip()]
    except (OSError, json.JSONDecodeError, AttributeError):
        return list(DEFAULT_KEYWORDS)
    return kws or list(DEFAULT_KEYWORDS)


REUSE_BAN_DAYS = 30       # 同じ参考動画の再利用を禁止する日数
MIN_DURATION = 45.0       # 完コピ台本に使える最低の実尺（秒）
MAX_DURATION = 180.0      # これより長い動画は完コピに向かない
MIN_VIEWS = 20_000        # これ未満は伸びた実績とみなさない
GOLDEN_RATIO = 100        # 黄金式: フォロワー×100 ＜ 再生数
# 退職ジャンルは母数が小さく、100倍を満たす動画は数日に1本しか出ない。
# 100倍が無い日は倍率の高い順に落として拾う（候補ゼロで止めない）。2026-08-07
FALLBACK_RATIO = 5.0      # これ以上の倍率なら「フォロワー比で伸びた」とみなす
# 上位選別のため、既定の6本出力時も従来から12本までは条件通過候補を集める。
# 呼び出し側が予備を含めて12本要求した時に24本まで探索が膨らまないよう、
# 採点母数は最低12本（または要求本数の大きい方）とする。
MIN_RANKING_POOL = 12
# Discord Bot 側の上限（2700秒）より十分手前で候補収集を切り上げ、取得済みの
# 候補をJSONへ保存する。外部タイムアウトでプロセスごと殺されるのを避ける。
DEFAULT_TIME_BUDGET = 2100
YT_DLP_FALLBACK_TIMEOUT = 45


# --- 数値パース -----------------------------------------------------------
_NUM_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*([万億KMBkmb]?)")
_MULT = {"": 1, "万": 10_000, "億": 100_000_000,
         "K": 1_000, "k": 1_000, "M": 1_000_000, "m": 1_000_000,
         "B": 1_000_000_000, "b": 1_000_000_000}


def parse_count(s: str) -> int | None:
    """「1.2万」「9,236」「73.5K」などを整数にする。取れなければ None。"""
    m = _NUM_RE.search(s or "")
    if not m:
        return None
    try:
        return int(float(m.group(1).replace(",", "")) * _MULT.get(m.group(2), 1))
    except ValueError:
        return None


def video_key(url: str) -> str:
    """再利用判定のキー。use_ref.py と同じ規則（/video/<数字>）。"""
    m = re.search(r"/video/(\d+)", url)
    return m.group(1) if m else url.strip().rstrip("/")


# --- ① 採用履歴（30日ルール） ---------------------------------------------
def recent_keys(days: int = REUSE_BAN_DAYS) -> set[str]:
    """直近 days 日以内に採用した動画のキー集合。ここに入るURLは候補から外す。"""
    if not HISTORY.exists():
        return set()
    limit = date.today() - timedelta(days=days)
    keys = set()
    for line in HISTORY.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) < 2 or line.startswith("#"):
            continue
        try:
            d = datetime.strptime(parts[0].strip(), "%Y-%m-%d").date()
        except ValueError:
            continue
        if d >= limit:
            keys.add(video_key(parts[1]))
    return keys


def covered_angles(limit: int = 20) -> list[str]:
    """直近の採用メモ（＝どんな角度を既に扱ったか）。重複判定のプロンプト材料。"""
    if not HISTORY.exists():
        return []
    rows = []
    for line in HISTORY.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) >= 4 and parts[3].strip():
            rows.append(f"{parts[0]} {parts[3].strip()[:120]}")
    return rows[-limit:]


# --- ② 検索グリッド --------------------------------------------------------
# グリッドから拾うのは動画URLだけにする。カードのテキストは通知サイドバーを
# 巻き込むことがあり当てにならず、アカウント名も href が数値UIDの場合がある。
# タイトル・投稿者・数値はすべて yt-dlp 側（probe）を正とする。
#
# ⚠️ **必ず検索結果コンテナの中だけを見ること。** ログイン済みプロファイルで開くと
# ページ全体には自分の動画へのリンク（サイドバー）が混ざり、`a[href*="/video/"]` を
# 素で使うと候補の半分が自分のチャンネルになる（2026-08-03に実際に発生）。
_GRID_JS = """
(() => {
  const root = document.querySelector('#main-content-search_video')
            || document.querySelector('[data-e2e="search_video-item"]')?.closest('div[class]')
            || document;
  const out = {};
  root.querySelectorAll('a[href*="/video/"]').forEach(a => {
    const m = a.href.match(/\\/@([^/]+)\\/video\\/(\\d+)/);
    if (m) out[m[2]] = {url: a.href.split('?')[0], author: m[1]};
  });
  return Object.values(out);
})()
"""

# 自分のチャンネル。参考動画に自分の動画を選んでも意味がないので必ず外す。
# ⚠️ アカウント名はコードに書かない（公開リポジトリなので）。`.env` の
#    TIKTOK_ACCOUNT から読む。詳細は own_account.py
OWN_ACCOUNTS = own_accounts()

# **リサーチ専用の未ログインChrome。** 投稿用（4_投稿/scripts/.chrome-profile・ポート9222）
# とは完全に分ける。業務アカウントでログインしたままスクレイピングすると、
# 何かあった時にそのアカウントごと巻き込むため（2026-08-03 社長判断）。
# 未ログインでも /search/video?q= と /search/user?q= は読めるので機能上の不都合はない。
RESEARCH_PROFILE = HERE / ".chrome-research"
RESEARCH_PORT = 9223


def interleave(items) -> list[dict]:
    """検索語ごとのグループから1本ずつ順番に取り出して1本のリストにする。"""
    groups: dict[str, list[dict]] = {}
    for it in items:
        groups.setdefault(it.get("keyword", ""), []).append(it)
    out, lists = [], list(groups.values())
    for i in range(max((len(g) for g in lists), default=0)):
        out.extend(g[i] for g in lists if i < len(g))
    return out


def search_grid(page, keyword: str, wait: float = 4.0) -> list[dict]:
    """検索の動画タブを開いてカードのURL・投稿者を集める。失敗したら空リスト。"""
    url = f"https://www.tiktok.com/search/video?q={keyword}"
    for attempt in (1, 2):
        try:
            page.goto(url, timeout=45_000)
            page.wait_for_timeout(int(wait * 1000))
            items = page.evaluate(_GRID_JS)
            if items:
                return items
            # CAPTCHA／「Please wait」は解かずに、同じURLをもう一度叩けば通ることが多い
            print(f"  … 「{keyword}」でカードが取れず。{attempt}回目、少し待って再試行")
            page.wait_for_timeout(5_000)
        except Exception as e:  # noqa: BLE001 - 1語の失敗で全体を止めない
            print(f"  ⚠ 検索失敗「{keyword}」: {str(e)[:120]}")
            return []
    return []


# --- ③ 実数を取る -----------------------------------------------------------
# **本線はブラウザ（未ログインの研究用Chrome）。yt-dlp は保険。** 2026-08-11 に入れ替えた。
# TikTokはyt-dlpのUAに対してbotチャレンジページを返す日があり、その日は probe が全滅して
# 候補ゼロになる（「参考動画の候補がゼロ」の主因はこれだった）。同じ時刻に実物のChromeでは
# 動画ページが普通に開けているので、ページ内の __UNIVERSAL_DATA_FOR_REHYDRATION__ から
# 直接読む。再生・いいね・コメント・保存・尺・投稿日・作者・**フォロワー**まで1回で取れる。
_DETAIL_JS = r"""
(() => {
  const el = document.getElementById('__UNIVERSAL_DATA_FOR_REHYDRATION__');
  if (!el) return null;
  const scope = (JSON.parse(el.textContent) || {})['__DEFAULT_SCOPE__'] || {};
  const detail = scope['webapp.video-detail'] || {};
  const item = detail.itemInfo && detail.itemInfo.itemStruct;
  if (!item) return null;
  const n = v => (v === undefined || v === null || v === '') ? null : Number(v);
  return {
    views: n(item.stats && item.stats.playCount),
    likes: n(item.stats && item.stats.diggCount),
    comments: n(item.stats && item.stats.commentCount),
    saves: n(item.stats && item.stats.collectCount),
    duration: n(item.video && item.video.duration),
    create_time: n(item.createTime),
    author: item.author && item.author.uniqueId,
    followers: n(item.authorStats && item.authorStats.followerCount),
    title: item.desc || '',
  };
})()
"""


def probe_page(page, url: str) -> dict | None:
    """動画ページを開いて埋め込みJSONから実数を取る。取れなければ None。"""
    for attempt in (1, 2):
        try:
            page.goto(url, timeout=45_000, wait_until="domcontentloaded")
            page.wait_for_timeout(3_000)
            # TikTokがgoto完了直後にクライアント側リダイレクトすることがある。
            # その瞬間のevaluateだけを再試行し、すぐ重いyt-dlpへ落とさない。
            for eval_attempt in range(3):
                try:
                    d = page.evaluate(_DETAIL_JS)
                    break
                except Exception as e:  # noqa: BLE001
                    if ("Execution context was destroyed" not in str(e)
                            or eval_attempt == 2):
                        raise
                    page.wait_for_timeout(1_000)
        except Exception as e:  # noqa: BLE001 - 1本の失敗で全体を止めない
            print(f"  ⚠ ページ取得失敗: {str(e)[:100]}")
            return None
        if d and d.get("views") is not None:
            ts = d.pop("create_time", None)
            d["upload_date"] = (datetime.fromtimestamp(ts).strftime("%Y%m%d") if ts else "")
            d["duration"] = float(d["duration"]) if d.get("duration") else None
            d["likes"] = d.get("likes") or 0
            d["comments"] = d.get("comments") or 0
            return d
        if attempt == 1:
            page.wait_for_timeout(4_000)   # 「Please wait」なら1回待てば通ることが多い
    return None


_PRINT_FMT = ("%(view_count)s\t%(duration)s\t%(upload_date)s\t%(like_count)s"
              "\t%(comment_count)s\t%(uploader)s\t%(title)s")


def probe(url: str, tries: int = 1,
          timeout: int = YT_DLP_FALLBACK_TIMEOUT) -> dict | None:
    """再生数・尺・投稿日・いいね・コメント・タイトルを取る。

    ブラウザ取得が本線なので、保険のyt-dlpは既定で1回・短い上限に留める。
    TikTokがyt-dlpだけを拒否する日に全候補で長時間粘らないため。
    """
    for i in range(1, tries + 1):
        try:
            p = subprocess.run(
                [str(YT_DLP), "--skip-download", "--no-warnings", "--print", _PRINT_FMT, url],
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            print(f"  ⚠ yt-dlp保険が{timeout}秒で時間切れ。次の候補へ進みます")
            return None
        if p.returncode == 0 and p.stdout.strip():
            f = p.stdout.strip().split("\t")
            if len(f) < 7:
                return None
            def _i(v: str) -> int | None:
                return int(v) if v.isdigit() else None
            return {
                "views": _i(f[0]),
                "duration": float(f[1]) if re.fullmatch(r"[\d.]+", f[1]) else None,
                "upload_date": f[2] if f[2].isdigit() else "",
                "likes": _i(f[3]) or 0,
                "comments": _i(f[4]) or 0,
                "author": f[5],          # 検索グリッドのhrefは数値UIDのことがあるのでこちらを正とする
                "title": f[6],
            }
        if i < tries:
            time.sleep(4 * i)
    return None


# --- ④ フォロワー数 --------------------------------------------------------
_USER_JS = "document.body.innerText.replace(/\\s+/g,' ').slice(0, 4000)"


def follower_count(page, author: str) -> int | None:
    """ユーザー検索タブから対象アカウントのフォロワー数を取る。

    プロフィールページ（/@user）は「Please wait...」で読めないことが多いので、
    未ログインでも数字が出る /search/user?q= を使う（2026-08-03の手順）。
    """
    try:
        page.goto(f"https://www.tiktok.com/search/user?q={author}", timeout=45_000)
        page.wait_for_timeout(3_500)
        txt = page.evaluate(_USER_JS)
    except Exception:  # noqa: BLE001
        return None
    # 「<ID> … 1.2万フォロワー」の並びから、対象IDに一番近いフォロワー数を拾う
    idx = txt.find(author)
    if idx < 0:
        return None
    m = re.search(r"([\d,.]+\s*[万億KMBkmb]?)\s*フォロワー", txt[idx:idx + 400])
    if not m:
        m = re.search(r"([\d,.]+\s*[万億KMBkmb]?)\s*[Ff]ollowers", txt[idx:idx + 400])
    return parse_count(m.group(1)) if m else None


# --- ⑤ 採点 ---------------------------------------------------------------
def score(c: dict) -> float:
    """並べ替え用のスコア。黄金式の倍率を軸に、Eng率で微調整する。"""
    ratio = c.get("golden_ratio") or 1.0
    eng = c.get("eng_rate") or 0.0
    return ratio * (1 + eng * 10)


def main() -> None:
    ap = argparse.ArgumentParser(description="参考動画の候補を自動収集して採点する")
    ap.add_argument("-o", "--out", default=None, help="候補JSONの出力先")
    ap.add_argument("--keywords", nargs="*", default=None,
                    help="検索語（既定は 検索語.json → DEFAULT_KEYWORDS の順）")
    ap.add_argument("--top", type=int, default=6, help="残す候補数")
    ap.add_argument("--probe-limit", type=int, default=48,
                    help="実数チェックにかける最大件数（条件通過が top×2 に達したら手前で止まる）")
    ap.add_argument("--time-budget", type=int, default=DEFAULT_TIME_BUDGET,
                    help="処理全体の時間枠（秒）。到達時は取得済み候補を保存して終了")
    args = ap.parse_args()

    if args.time_budget <= 0:
        ap.error("--time-budget は1以上を指定してください")
    deadline = time.monotonic() + args.time_budget

    if not YT_DLP.exists():
        sys.exit(f"[エラー] yt-dlp が無い: {YT_DLP}")

    import browser_ctx  # noqa: PLC0415 - playwright入りvenvでのみ解決する
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    keywords = args.keywords or search_keywords()
    skip = recent_keys()
    src = "既定" if (args.keywords or not KEYWORDS_JSON.exists()) else "週次レビュー"
    print(f"■ 検索語 {len(keywords)}件（{src}）/ 30日以内の採用済み {len(skip)}件は除外")
    print("  " + " / ".join(keywords))

    found: dict[str, dict] = {}
    proc = None
    try:
        with sync_playwright() as p:
            # 未ログインの専用プロファイルで開く（投稿アカウントは巻き込まない）。
            # 前のセッションの開きっぱなしChromeは固まっていることがあるので、
            # 駄目なら勝手に起動し直す（無人実行では人が直せないため）
            _browser, _ctx, page, proc = browser_ctx.connect_healthy(
                p, profile_dir=RESEARCH_PROFILE, port=RESEARCH_PORT)

            # ① 検索
            for kw in keywords:
                if time.monotonic() >= deadline:
                    print("⚠ 時間枠に達したため、残りの検索語を打ち切ります")
                    break
                items = search_grid(page, kw)
                new = 0
                for it in items:
                    k = video_key(it["url"])
                    if k in skip or k in found:
                        continue
                    it["key"] = k
                    it["keyword"] = kw
                    found[k] = it
                    new += 1
                print(f"  「{kw}」 {len(items)}件中 新規{new}件")
                time.sleep(1.5)

            print(f"■ 重複除去後の候補: {len(found)}件")
            if not found:
                print("⚠ 候補ゼロ。TikTok側でブロックされている可能性（時間を空けて再実行）")

            # ② 実数を取る。
            # 先頭から素直に切ると最初の1〜2語だけで枠を使い切るので、検索語ごとに
            # 順番に1本ずつ拾って混ぜる（後ろの語の動画が一度も見られないのを防ぐ）。
            # **必要な数が溜まったら止める／溜まらなければ上限まで掘る**。固定で上位N件だけ
            # 見ていた頃は、その枠がたまたま短尺・再生不足ばかりだと、後ろに100件残したまま
            # 候補ゼロになっていた（2026-08-11）。
            cands = interleave(found.values())
            enough = max(args.top, MIN_RANKING_POOL)
            budget = min(len(cands), args.probe_limit)
            alive = []
            for i, c in enumerate(cands, 1):
                if time.monotonic() >= deadline:
                    print(f"  ⚠ 時間枠に達したため{i - 1}件で打ち切り"
                          f"（条件通過{len(alive)}件）")
                    break
                if len(alive) >= enough or i > args.probe_limit:
                    print(f"  … {i - 1}件見て条件通過{len(alive)}件。ここで打ち切り"
                          f"（未チェック{len(cands) - i + 1}件）")
                    break
                st = probe_page(page, c["url"]) or probe(c["url"])
                if not st:
                    print(f"  [{i}/{budget}] DL不可 {c['author']}")
                    continue
                c.update(st)
                if c["author"] in OWN_ACCOUNTS:
                    print(f"  [{i}/{budget}] 自分の動画なので除外 {c['author']}")
                    continue
                if not c["views"] or c["views"] < MIN_VIEWS:
                    print(f"  [{i}/{budget}] 再生不足 {c['views'] or 0:,} {c['author']}")
                    continue
                if not c["duration"] or not (MIN_DURATION <= c["duration"] <= MAX_DURATION):
                    print(f"  [{i}/{budget}] 尺が対象外 {c['duration']}s {c['author']}")
                    continue
                c["eng_rate"] = (c["likes"] + c["comments"]) / c["views"]
                alive.append(c)
                print(f"  [{i}/{budget}] ✓ {c['views']:,}再生 {c['duration']:.0f}s {c['author']}")
                time.sleep(1.0)

            # ③ フォロワー（黄金式）。上位候補だけに絞って叩く。
            # 動画ページ由来なら既に入っているので、その時はユーザー検索を省く（2026-08-11）
            alive.sort(key=lambda c: -c["views"])
            for c in alive[: args.top * 3]:
                if time.monotonic() >= deadline:
                    print("  ⚠ 時間枠に達したため、残りのフォロワー取得を省略します")
                    break
                f = c.get("followers") or follower_count(page, c["author"])
                c["followers"] = f
                c["golden_ratio"] = (c["views"] / f) if f else None
                c["golden_ok"] = bool(f and f * GOLDEN_RATIO < c["views"])
                time.sleep(1.2)
    finally:
        if proc:
            proc.terminate()

    # ④ 黄金式を通ったものを上に、足りない分を緩い段から順に**継ぎ足して** --top まで埋める。
    # 以前は「followers が取れなかったもの」だけをフォールバックにしていたため、
    # フォロワーが正常に取れているほど候補ゼロになる逆転が起きていた（2026-08-07 修正）。
    # さらに、段は上から1つだけ選ぶ作りだったので、100倍が1本の日は**候補も1本しか出ず**、
    # 台本化で季節性NG／角度重複と判定された時点で打ち止めになっていた（2026-08-11 修正）。
    # 2_台本生成 は候補を上から順に試すので、順番さえ守れば下の段を混ぜても害はない。
    strict = [c for c in alive if c.get("golden_ok")]
    loose = [c for c in alive if c not in strict
             and ((c.get("golden_ratio") or 0) >= FALLBACK_RATIO
                  or c.get("followers") is None)]
    rest = [c for c in alive if c not in strict and c not in loose]
    strict.sort(key=score, reverse=True)
    loose.sort(key=score, reverse=True)
    rest.sort(key=lambda c: -(c["views"] or 0))   # 倍率が無いので再生数で並べる
    ranked = (strict + loose + rest)[: args.top]
    print(f"■ 採用基準: 黄金式(100倍) {len(strict)}件 / "
          f"緩和({FALLBACK_RATIO:.0f}倍以上・フォロワー不明) {len(loose)}件 / "
          f"再生数のみ {len(rest)}件 → 上位{len(ranked)}件")

    for c in ranked:
        r = c.get("golden_ratio")
        print(f"★ {c['views']:,}再生 黄金式{f'{r:.1f}倍' if r else '不明'} "
              f"Eng{c['eng_rate']*100:.2f}% {c['duration']:.0f}s {c['url']}")

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "keywords": keywords,
        "covered_angles": covered_angles(),
        "candidates": ranked,
    }
    out = Path(args.out) if args.out else HERE.parent / f"候補_{date.today():%Y%m%d}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✔ 候補 {len(ranked)}件 → {out}")
    if not ranked:
        sys.exit(2)  # 呼び出し側（Discord bot）がフォールバックに切り替えられるように


if __name__ == "__main__":
    main()
