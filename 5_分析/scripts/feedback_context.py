#!/usr/bin/env python3
"""5_分析 → 1_リサーチ フィードバックの「材料集め」。

このスクリプトはAIに意見を出させる工程の“お膳立て”をする。実際の提案（次のネタ・
勝ちパターンの横展開）は、集めた材料 + プロンプト_フィードバック.md に従って AI が書く。
2_台本生成 の「プロンプト_台本生成.md + format_script.py」と同じ役割分担:
  - 決まりきった収集・整形 = スクリプト（ここ）
  - 判断・提案（人間の承認前の下書き）= AI

やること:
  metrics.csv / 投稿ログ / 採用履歴 / 参考動画リスト / 過去台本 を1つのMarkdown束にまとめ、
  同時に「今どれだけ実測データがあるか（＝データ駆動できるか一般論止まりか）」を判定して先頭に出す。
  → メモリ roadmap-decisions-2026-07 の「データが溜まるまでは一般論」を仕組みで担保する。
  → 提案の採用/却下は社長（メモリ act-step-human-gate）。ここはあくまで下書きの材料。

標準ライブラリのみ（venv不要、python3一発）。analyze.py の勝ち負け判定を再利用する。

使い方:
  cd 5_分析/scripts
  python3 feedback_context.py            # 材料束をターミナルに表示
  python3 feedback_context.py --md       # 5_分析/レポート/フィードバック素材_YYYYMMDD.md にも保存

このあとの流れ（AIがやる）:
  1) 出力された材料束と ../プロンプト_フィードバック.md を読む
  2) 1_リサーチ/ネタ提案_YYYYMMDD.md に提案を書く
  3) 社長が OK/却下（採用したものだけ use_ref.py 等で次工程へ）
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta
from pathlib import Path

import analyze  # 同ディレクトリ。build_report / load を再利用（勝ち負け判定のDRY化）

ROOT = Path(__file__).resolve().parents[2]
METRICS = ROOT / "5_分析" / "metrics.csv"
POST_LOG = ROOT / "4_投稿" / "ログ" / "post_log.csv"
ADOPT_TSV = ROOT / "1_リサーチ" / "採用履歴.tsv"
REF_LIST = ROOT / "1_リサーチ" / "参考動画リスト.md"
SCRIPTS_DIR = ROOT / "2_台本生成"
REPORT_DIR = ROOT / "5_分析" / "レポート"
REUSE_LOCK_DAYS = 30  # 参考動画の再利用禁止期間（メモリ ref-video-no-reuse-30days）


def _has_retention(row: dict) -> bool:
    v = (row.get("視聴維持率", "") or "").strip()
    try:
        return float(v) > 0
    except ValueError:
        return False


def data_gauge(rows: list[dict]) -> list[str]:
    """実測の充足度を判定して先頭サマリを作る。

    「維持率が入っている本数」を基準にモードを切り替える。維持率は自動取得できず
    手入力（record.py）なので、これが入っている＝ちゃんと測った本数。
    """
    total = len(rows)
    measured = sum(1 for r in rows if _has_retention(r))
    if measured == 0:
        mode = "🅐 一般論モード（実測ゼロ）"
        note = ("自分の投稿の実測がまだ無い。提案は参考動画・過去台本・アルゴリズム知識ベースの"
                "“仮説”に留める。断定せず「試す価値のある型」として出すこと。")
    elif measured < 5:
        mode = f"🅑 データ僅少モード（維持率入り {measured}本）"
        note = "傾向は参考程度。強い結論は出さず、参考動画の型＋わずかな自データで仮説を補強する。"
    elif measured < 10:
        mode = f"🅒 初期データモード（維持率入り {measured}本）"
        note = "弱い傾向が見え始める。勝ち動画の共通項を“候補”として提示してよい。"
    else:
        mode = f"🅓 データ駆動モード（維持率入り {measured}本）"
        note = "自データ主導で勝ちパターンを結論づけてよい。参考動画は補助に回す。"
    return [
        "## 0. データ充足度（提案の強さの上限）",
        "",
        f"- 記録済み: **{total}本**（うち維持率入り **{measured}本**）",
        f"- モード: **{mode}**",
        f"- 指示: {note}",
        "",
    ]


def section_analyze() -> list[str]:
    """analyze.py の勝ち/負けレポートをそのまま挿む。"""
    try:
        rows = analyze.load(None)
    except SystemExit:
        rows = []
    body = analyze.build_report(rows) if rows else ["（metrics.csv に実データなし）"]
    return ["## 1. 自動判定レポート（analyze.py）", "", *body, ""]


def section_posted_appeals() -> list[str]:
    """実際に投稿した訴求（キャプション）の一覧。何を既に打ったかの把握用。"""
    out = ["## 2. これまで投稿した訴求（post_log のキャプション）", ""]
    if not POST_LOG.exists():
        out += ["（投稿ログなし）", ""]
        return out
    seen: list[str] = []
    with POST_LOG.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            cap = (r.get("caption", "") or "").strip()
            status = (r.get("status", "") or "")
            if not cap or cap in seen or status in ("dry_run", "precheck_failed"):
                continue
            seen.append(cap)
            out.append(f"- [{r.get('video','')}] {cap}")
    if len(out) == 2:
        out.append("（投稿済みの訴求なし）")
    out.append("")
    return out


def section_adopt_history() -> list[str]:
    """採用済み参考動画と30日ロックの解除日。同じ型の再利用回避に使う。"""
    out = ["## 3. 参考動画の採用履歴（30日再利用ロック状況）", ""]
    if not ADOPT_TSV.exists():
        out += ["（採用履歴なし）", ""]
        return out
    today = date.today()
    for line in ADOPT_TSV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) < 2:
            continue
        adopted_s, url = cols[0], cols[1]
        memo = cols[3] if len(cols) > 3 else ""
        try:
            unlock = datetime.strptime(adopted_s, "%Y-%m-%d").date() + timedelta(days=REUSE_LOCK_DAYS)
            locked = "🔒ロック中" if today < unlock else "✅解除済み"
            state = f"{locked}（〜{unlock.isoformat()}）"
        except ValueError:
            state = "日付不明"
        out.append(f"- {adopted_s} {url} — {state}")
        if memo:
            out.append(f"    - メモ: {memo}")
    out.append("")
    return out


def section_past_scripts() -> list[str]:
    """過去台本のフック（1行目）一覧。既に使った切り口＝重複回避と横展開の材料。"""
    out = ["## 4. 過去台本のフック（1行目＝冒頭の掴み）", ""]
    files = sorted(SCRIPTS_DIR.glob("台本_*.txt"))
    if not files:
        out += ["（過去台本なし）", ""]
        return out
    for fp in files:
        first = ""
        for ln in fp.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                first = ln.strip()
                break
        out.append(f"- **{fp.stem}**: {first}")
    out.append("")
    return out


def section_ref_pool() -> list[str]:
    """参考動画リストの“候補/見送り”を材料として案内（本文は長いのでポインタ）。

    リストは人が精査した勝ち型の宝庫。全文貼りは冗長なので参照先を示し、AIには
    「候補・見送り欄も読んで次の型を拾え」と促す。
    """
    out = ["## 5. 参考動画プール（次の型の在庫）", ""]
    if REF_LIST.exists():
        # 見出し行だけ拾って地図を出す（採用/候補/見送り のどこに何があるか）
        heads = [ln.strip() for ln in REF_LIST.read_text(encoding="utf-8").splitlines()
                 if ln.strip().startswith("###")]
        out.append(f"- 全文: `1_リサーチ/参考動画リスト.md`（下記セクションを読む）")
        for h in heads:
            out.append(f"    - {h.lstrip('# ').strip()}")
    else:
        out.append("- （参考動画リストなし）")
    out.append("")
    return out


def build(rows: list[dict]) -> str:
    lines = [
        f"# フィードバック材料束  生成 {datetime.now():%Y-%m-%d %H:%M}",
        "",
        "> これは 5_分析→1_リサーチ の提案を書くための“材料”。判断・提案は "
        "`5_分析/プロンプト_フィードバック.md` に従ってAIが `1_リサーチ/ネタ提案_YYYYMMDD.md` に書く。",
        "> 提案の採用/却下は社長（人間承認ゲート）。",
        "",
    ]
    lines += data_gauge(rows)
    lines += section_analyze()
    lines += section_posted_appeals()
    lines += section_adopt_history()
    lines += section_past_scripts()
    lines += section_ref_pool()
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="フィードバック用の材料束を集める")
    ap.add_argument("--md", action="store_true",
                    help="レポート/フィードバック素材_YYYYMMDD.md にも保存")
    args = ap.parse_args()

    try:
        rows = analyze.load(None)
    except SystemExit:
        rows = []
    text = build(rows)
    print(text)

    if args.md:
        REPORT_DIR.mkdir(exist_ok=True)
        out = REPORT_DIR / f"フィードバック素材_{date.today():%Y%m%d}.md"
        out.write_text(text + "\n", encoding="utf-8")
        print(f"\n✓ 保存 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
