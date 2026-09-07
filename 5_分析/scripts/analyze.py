#!/usr/bin/env python3
"""5_分析: metrics.csv を読んで勝ち/負けパターンを判定しレポートを出す。

使い方:
  ./analyze.py                 # 全データを分析してターミナルに表示
  ./analyze.py --month 2026-07 # 指定月のみ
  ./analyze.py --md            # レポート/分析レポート_YYYYMM.md にも書き出す

判定の考え方（メモリ tiktok-analytics-method / tiktok-algorithm-guide 準拠）:
  - 視聴維持率: 尺で目標が変わる。README方針=40〜50秒で70〜80%,20秒で80%は理想値。
    現実的な合格ラインの下限は15%（実務基準）。ここでは「理想/現実」の二段で表示。
  - 継続率1秒: 40%以上が目標。冒頭(フック)の良し悪しの指標。
  - おすすめ流入率: 95%以上が目安。低いとシャドウバンやフォロワー依存を疑う。
最重要は視聴維持率と継続率。いいね等はサブ評価。
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

METRICS = Path(__file__).resolve().parent.parent / "metrics.csv"
REPORT_DIR = Path(__file__).resolve().parent.parent / "レポート"

# しきい値
HOLD1S_TARGET = 40.0      # 継続率1秒(%) 目標
FYP_TARGET = 95.0         # おすすめ流入率(%) 目安
RETENTION_FLOOR = 15.0    # 視聴維持率(%) 現実的な合格下限（実務基準）


def retention_ideal(duration: float) -> float:
    """尺から理想の視聴維持率(%)を返す（READMEの方針）。"""
    if duration <= 0:
        return 0.0
    if duration <= 25:
        return 80.0
    if duration <= 55:
        return 75.0  # 40〜50秒帯 70〜80% の中央
    return 60.0      # 長尺はやや緩める


def f(val: str) -> float | None:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def load(month: str | None) -> list[dict]:
    if not METRICS.exists():
        print(f"metrics.csv がありません: {METRICS}", file=sys.stderr)
        print("先に record.py で1本以上記録してください。", file=sys.stderr)
        sys.exit(1)
    rows = []
    with METRICS.open(encoding="utf-8") as fp:
        for r in csv.DictReader(fp):
            if month and not (r.get("投稿日時", "") or "").startswith(month):
                continue
            rows.append(r)
    return rows


def judge(r: dict) -> tuple[str, list[str]]:
    """1本を評価してラベル(勝ち/普通/要改善)と指摘リストを返す。"""
    notes: list[str] = []
    score = 0
    dur = f(r.get("尺秒", "")) or 0
    ret = f(r.get("視聴維持率", ""))
    hold = f(r.get("継続率1秒", ""))
    fyp = f(r.get("おすすめ流入率", ""))

    if ret is not None:
        ideal = retention_ideal(dur)
        if ret >= ideal:
            score += 2; notes.append(f"維持率{ret:.0f}%◎(理想{ideal:.0f}%)")
        elif ret >= RETENTION_FLOOR:
            score += 0; notes.append(f"維持率{ret:.0f}%△(下限{RETENTION_FLOOR:.0f}%は超,理想{ideal:.0f}%)")
        else:
            score -= 1; notes.append(f"維持率{ret:.0f}%✗ 尺/テンポ見直し")
    if hold is not None:
        if hold >= HOLD1S_TARGET:
            score += 1; notes.append(f"継続率{hold:.0f}%◎")
        else:
            score -= 1; notes.append(f"継続率{hold:.0f}%✗ 冒頭1秒(フック)改善")
    if fyp is not None and fyp < FYP_TARGET:
        notes.append(f"おすすめ{fyp:.0f}%↓ 投稿時間変更/シャドウバン確認")

    label = "勝ち" if score >= 3 else ("要改善" if score < 0 else "普通")
    return label, notes


def fmt_pct(v: str) -> str:
    n = f(v)
    return f"{n:.0f}%" if n is not None else "―"


def fmt_num(v: str) -> str:
    n = f(v)
    if n is None:
        return "―"
    return f"{int(n)}" if n == int(n) else f"{n}"


def build_report(rows: list[dict]) -> list[str]:
    lines: list[str] = []
    if not rows:
        return ["対象データがありません。"]

    # 1本ずつの評価
    judged = [(r, *judge(r)) for r in rows]
    lines.append(f"# TikTok分析レポート  対象 {len(rows)} 本\n")

    lines.append("## 動画別サマリー\n")
    lines.append("| 動画 | 尺 | 再生 | 維持率 | 継続1s | おすすめ | 保存 | LINE | 主要視聴者 | 一致 | 判定 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for r, label, _notes in judged:
        lines.append(
            f"| {r.get('動画名','')} | {fmt_num(r.get('尺秒',''))}s "
            f"| {fmt_num(r.get('再生数',''))} | {fmt_pct(r.get('視聴維持率',''))} "
            f"| {fmt_pct(r.get('継続率1秒',''))} | {fmt_pct(r.get('おすすめ流入率',''))} "
            f"| {fmt_num(r.get('保存',''))} | {fmt_num(r.get('LINE登録',''))} "
            f"| {r.get('主要視聴者','') or '―'} | {r.get('ターゲット一致','') or '―'} | {label} |"
        )
    lines.append("")

    # 指摘
    lines.append("## 各動画の指摘\n")
    for r, label, notes in judged:
        joined = " / ".join(notes) if notes else "数値未入力"
        lines.append(f"- **{r.get('動画名','')}**（{label}）: {joined}")
    lines.append("")

    # 勝ち/負けの抽出
    wins = [r for r, l, _ in judged if l == "勝ち"]
    bads = [r for r, l, _ in judged if l == "要改善"]
    lines.append("## 勝ちパターン → 1_リサーチ へ横展開\n")
    if wins:
        for r in wins:
            aud = r.get("主要視聴者", "")
            aud_txt = f"／届いた層: {aud}" if aud else ""
            lines.append(f"- {r.get('動画名','')}（維持率{fmt_pct(r.get('視聴維持率',''))}）"
                         f"の型・フック・テーマを次の企画に流用{aud_txt}。")
    else:
        lines.append("- まだ「勝ち」判定の動画なし。データを貯めて基準到達を狙う。")
    lines.append("")

    # 視聴者属性の活用（動画のステップ3）: ターゲットに届いた動画を軸に横展開
    on_target = [r for r in rows if (r.get("ターゲット一致", "") or "").startswith("○")]
    off_target = [r for r in rows if (r.get("ターゲット一致", "") or "").startswith("✗")]
    lines.append("## 視聴者属性（ターゲット到達）\n")
    if on_target:
        lines.append("**ターゲットに届いた動画（○）→ この軸で横展開してペルソナ精度UP:**")
        for r in on_target:
            lines.append(f"- {r.get('動画名','')}: {r.get('主要視聴者','') or '属性未入力'}")
    if off_target:
        lines.append("\n**ターゲットとズレた動画（✗）→ 訴求/切り口の再確認:**")
        for r in off_target:
            lines.append(f"- {r.get('動画名','')}: {r.get('主要視聴者','') or '属性未入力'}")
    if not on_target and not off_target:
        lines.append("- 視聴者タブの属性（性別/年齢/位置）とターゲット一致(○/△/✗)を record 時に入れると、"
                     "「ターゲットに届いた動画」を特定して横展開できる。")
    lines.append("")
    lines.append("## 要改善\n")
    if bads:
        for r in bads:
            _, notes = judge(r)
            lines.append(f"- {r.get('動画名','')}: {' / '.join(notes)}")
    else:
        lines.append("- 要改善判定なし。")
    lines.append("")
    return lines


def main() -> int:
    p = argparse.ArgumentParser(description="metrics.csv を分析")
    p.add_argument("--month", help="YYYY-MM で投稿月を絞る")
    p.add_argument("--md", action="store_true", help="レポートを .md でも保存")
    args = p.parse_args()

    rows = load(args.month)
    lines = build_report(rows)
    text = "\n".join(lines)
    print(text)

    if args.md:
        REPORT_DIR.mkdir(exist_ok=True)
        tag = args.month or "all"
        out = REPORT_DIR / f"分析レポート_{tag}.md"
        out.write_text(text + "\n", encoding="utf-8")
        print(f"\n✓ 保存 → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
