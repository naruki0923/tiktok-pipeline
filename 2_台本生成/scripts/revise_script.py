#!/usr/bin/env python3
"""できあがった台本に、社長の指示で手直しを入れる（Discordの「直して」用）。

    ./.venv/bin/python revise_script.py --name 本番_036 --instruction "タイトルをもっと短く"

write_script.py との違いは、リサーチも季節性判定もしないこと。**既にある台本を
直すだけ**なので、LLM呼び出しは1回で済む（[[llm-cost-minimize-claude-cli]]）。

やること: 現行の台本＋ルール＋指示 → LLMが直した台本 → 正規化 → format_script
        → キャプション更新 → JSONで結果を返す（動画の作り直しは呼び出し側）。

元の台本は 台本_NNN.bak.txt に退避するので、おかしくなったら戻せる。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import llm  # noqa: E402
import write_script as w  # noqa: E402  （正規化・整形・キャプション組み立てを共有する）

PROMPT = """あなたは退職給付金ジャンルのTikTok台本ライターです。
既にある台本に、依頼された手直しを入れてください。

# 手直しの依頼（これが最優先）
{instruction}

# 守ること
- **依頼された箇所以外は変えない。** 全面的に書き直さないでください。
- 台本作成ルール（下記）は引き続きすべて守る。特に:
  - 固定文言（いいね保存の誘導・中盤のフォロー誘導・末尾のCTA）は文言を変えない
  - 敬体は です/ます。断定・誇大表現（「必ずもらえる」等）は使わない
  - **空白を除いて450〜500字**に収める（520字を超えたら失格）
- **書式**: 句読点のある普通の文章で書き、**文の途中で改行しない**。
  テロップを割りたい位置には改行ではなく**読点「、」**を打つ。
  - ✗ `退職する人は全員\\n知っておくべき\\n退職給付金の制度があります`
  - ○ `退職する人は全員、知っておくべき、退職給付金の制度があります。`
- **フックの1文目は読点で2〜3個のフレーズに割る。** ここがタイトルカードになる。
  1フレーズだけにしないこと（タイトルが1行になって崩れる）。
  保存を煽る「特に最後の…」は必ず**2文目**にする（1文目に混ぜない）。

# 台本作成ルール（全文）
{rules}

# 今の台本
{current}

# 出力形式（JSONのみ）
{{
  "script": "直した台本の全文（句読点あり・改行は\\nで表す）",
  "title": "動画タイトル。20〜30字。【】は付けない",
  "tags": ["退職", "失業保険", "..."],
  "note": "何をどう直したかを1文で"
}}
"""


def main() -> None:
    ap = argparse.ArgumentParser(description="既存の台本に手直しを入れる")
    ap.add_argument("--name", required=True, help="対象（例: 本番_036）")
    ap.add_argument("--instruction", required=True, help="直してほしい内容")
    args = ap.parse_args()

    num = args.name.split("_")[-1]
    raw_path = w.GEN_DIR / f"台本_{num}.txt"
    if not raw_path.exists():
        print(json.dumps({"ok": False, "reason": f"台本が見つかりません: {raw_path.name}"},
                         ensure_ascii=False))
        sys.exit(3)

    current = raw_path.read_text(encoding="utf-8").strip()
    print(f"■ {args.name} を手直し / LLM経路: {llm.backend()}", file=sys.stderr)
    print(f"■ 指示: {args.instruction}", file=sys.stderr)

    try:
        res = llm.ask_json(PROMPT.format(
            instruction=args.instruction,
            rules=w.RULES.read_text(encoding="utf-8"),
            current=current,
        ), max_tokens=6000)
    except llm.LLMError as e:
        print(json.dumps({"ok": False, "reason": f"LLMエラー: {e}"}, ensure_ascii=False))
        sys.exit(3)

    script = (res.get("script") or "").strip()
    if len(script) < 200:
        print(json.dumps({"ok": False, "reason": f"直した台本が短すぎます（{len(script)}字）"},
                         ensure_ascii=False))
        sys.exit(3)

    # 元に戻せるよう退避してから上書きする
    (w.GEN_DIR / f"台本_{num}.bak.txt").write_text(current + "\n", encoding="utf-8")
    raw_path.write_text(w.normalize_raw(script), encoding="utf-8")

    out_path = w.SCRIPT_TXT_DIR / f"{args.name}.txt"
    formatted, log = w.run_format(raw_path, out_path)
    print(log, file=sys.stderr)

    caption = w.build_caption(res.get("title", ""), res.get("tags") or [])
    if res.get("title"):
        for suffix in ("", "_TikTok"):
            (w.CAPTION_DIR / f"{args.name}{suffix}.txt").write_text(caption + "\n", encoding="utf-8")

    print(json.dumps({
        "ok": True,
        "name": args.name,
        "note": res.get("note", ""),
        "title": res.get("title", ""),
        "title_lines": w.title_lines_of(formatted),
        "caption": caption,
        "warnings": [x.strip("- ").strip() for x in log.splitlines() if x.strip().startswith("-")],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
