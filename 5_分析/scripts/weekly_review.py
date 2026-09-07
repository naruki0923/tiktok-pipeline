#!/usr/bin/env python3
"""5_分析: 週次レビュー（Check → Act）を1コマンドで通す。

毎週日曜22:00に Discord Bot が呼ぶ。人がやっていた「実測を取り直す→材料を集める→
何を変えるか決める→リサーチと台本に反映する」を1本にまとめたもの。

やること（この順）:
  ① 実測の取り直し … update_results.py（5_分析/実績.tsv を最新に）
  ② 材料集め       … feedback_context.py（metrics/投稿ログ/採用履歴/参考動画プール）
  ③ 判断           … LLMを**1回だけ**呼ぶ（プロンプト_週次反映.md）
  ④ 反映           … 検索語.json / 重点方針.md を書き換える（＝次の生成から効く）
  ⑤ 下書き         … 1_リサーチ/ネタ提案_YYYYMMDD.md（採用/却下は社長）

**自動で書き換えるのは②つのファイルだけ**:
  - `1_リサーチ/検索語.json`   … auto_research.py の検索語（無ければ既定6語のまま）
  - `2_台本生成/重点方針.md`   … write_script.py が台本プロンプトに差し込む

`台本作成ルール.md` や各スクリプトのコードには触らない。人が書いたものを機械が
書き換えると戻せなくなるので、**機械が書く場所を分けてある**。どちらも上書き前の
中身を週次レポートに丸ごと残すので、git で戻せる。

使い方（venv不要。python3 一発。①だけ 4_投稿 の venv を内部で呼ぶ）:
    cd 5_分析/scripts
    python3 weekly_review.py                # 通し（Chromeを使う。10〜20分）
    python3 weekly_review.py --no-fetch     # ①を飛ばす（Chromeを使わない）
    python3 weekly_review.py --dry-run      # 反映せず、何が変わるかだけ見る

最後の1行に結果のJSONを出す（Discord Bot はこれを読む）。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent              # 5_分析/scripts
ROOT = HERE.parents[1]                              # プロジェクトルート
GEN_SCRIPTS = ROOT / "2_台本生成" / "scripts"
POST_PY = ROOT / "4_投稿" / "scripts" / ".venv" / "bin" / "python"   # playwright入り
UPDATE_RESULTS = HERE / "update_results.py"

PROMPT_MD = ROOT / "5_分析" / "プロンプト_週次反映.md"
REPORT_DIR = ROOT / "5_分析" / "レポート"
KEYWORDS_JSON = ROOT / "1_リサーチ" / "検索語.json"      # auto_research.py が読む
FOCUS_MD = ROOT / "2_台本生成" / "重点方針.md"           # write_script.py が読む
PROPOSAL_DIR = ROOT / "1_リサーチ"

FETCH_LIMIT = 30            # 実測を取り直す本数（1本20〜40秒かかる）
FETCH_TIMEOUT = 2400        # ①の上限（秒）。超えたら古い実績のまま先へ進む
MIN_KEYWORDS, MAX_KEYWORDS = 4, 8
MAX_KEYWORD_LEN = 20
MAX_FOCUS_CHARS = 1200      # 台本プロンプトを膨らませすぎない上限

sys.path.insert(0, str(HERE))            # feedback_context / analyze
sys.path.insert(0, str(GEN_SCRIPTS))     # llm / write_script

import feedback_context  # noqa: E402
import llm  # noqa: E402
import write_script  # noqa: E402  (covered_angles を再利用。実績の見方を1か所に保つ)


class ReviewError(RuntimeError):
    """週次レビューを続けられない失敗。呼び出し側（Bot）が通知に回す。"""


# --- ① 実測の取り直し -------------------------------------------------------
def fetch_results(limit: int) -> str:
    """update_results.py を回して 実績.tsv を最新にする。失敗しても止めない。

    TikTokは日によってブロックしてくるし、Chromeが固まっていることもある。
    ここでコケても**先週までの実績で判断は続けられる**ので、警告だけ残して進む。
    """
    if not POST_PY.exists():
        return f"⚠️ 実測の取り直しを飛ばしました（{POST_PY} が無い）"
    try:
        p = subprocess.run(
            ["/usr/bin/caffeinate", "-i", "-m", "-s",
             str(POST_PY), str(UPDATE_RESULTS), "--limit", str(limit)],
            cwd=str(HERE), capture_output=True, text=True, timeout=FETCH_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return f"⚠️ 実測の取り直しがタイムアウト（{FETCH_TIMEOUT}秒）。前回の実績で進めます"
    except OSError as e:
        return f"⚠️ 実測の取り直しを起動できません: {e}"
    if p.returncode != 0:
        tail = ((p.stdout or "") + (p.stderr or ""))[-400:]
        return f"⚠️ 実測の取り直しに失敗（前回の実績で進めます）\n{tail}"
    return ""


# --- ② 材料集め -------------------------------------------------------------
def materials() -> str:
    """feedback_context の材料束。metrics.csv が空でも落とさない。"""
    try:
        rows = feedback_context.analyze.load(None)
    except SystemExit:      # metrics.csv がまだ無い
        rows = []
    return feedback_context.build(rows)


# --- ③ 判断（LLM 1回） ------------------------------------------------------
def build_prompt(mats: str) -> str:
    """プロンプト_週次反映.md の差し込み口を埋める。

    本文にJSONの例（波かっこ）が入っているので str.format は使えない。
    置き換える語を決め打ちしている。
    """
    text = PROMPT_MD.read_text(encoding="utf-8")
    for token, value in (
        ("{today}", f"{date.today():%Y年%m月%d日}（{'月火水木金土日'[date.today().weekday()]}）"),
        ("{current_keywords}", "\n".join(f"- {k}" for k in current_keywords())),
        ("{current_focus}", FOCUS_MD.read_text(encoding="utf-8") if FOCUS_MD.exists()
         else "（まだ無し。今回が初回）"),
        ("{angles}", write_script.covered_angles()),
        ("{materials}", mats[:20000]),
    ):
        text = text.replace(token, value)
    return text


# --- ④ 反映 -----------------------------------------------------------------
def current_keywords() -> list[str]:
    """いま auto_research.py が使う検索語。JSONが無ければ既定値を読む。"""
    if KEYWORDS_JSON.exists():
        try:
            data = json.loads(KEYWORDS_JSON.read_text(encoding="utf-8"))
            kws = [str(k) for k in data.get("keywords", []) if str(k).strip()]
            if kws:
                return kws
        except (json.JSONDecodeError, AttributeError):
            pass
    sys.path.insert(0, str(ROOT / "1_リサーチ" / "scripts"))
    try:
        import auto_research  # noqa: PLC0415 - playwright は main の中でしか要らない
        return list(auto_research.DEFAULT_KEYWORDS)
    except Exception:  # noqa: BLE001 - 既定値が読めなくても週次は続ける
        return []


def clean_keywords(raw) -> list[str]:
    """LLMの返した検索語を検品する。1語でも怪しければ全体を捨てる。

    検索語は無人のリサーチが唯一入口にする値なので、変な語を入れると
    **翌朝から1本も作れなくなる**。緩く直すより、丸ごと据え置く方が安全。
    """
    if not isinstance(raw, list):
        return []
    kws: list[str] = []
    for item in raw:
        k = str(item).strip()
        if not k or len(k) > MAX_KEYWORD_LEN or "http" in k or "\n" in k:
            return []
        if k not in kws:
            kws.append(k)
    return kws if MIN_KEYWORDS <= len(kws) <= MAX_KEYWORDS else []


def apply_keywords(kws: list[str], dry: bool) -> str:
    """検索語.json を書き換える。変化が無ければ空文字を返す。"""
    before = current_keywords()
    if not kws or kws == before:
        return ""
    if not dry:
        KEYWORDS_JSON.write_text(json.dumps({
            "updated": f"{date.today():%Y-%m-%d}",
            "source": "5_分析/scripts/weekly_review.py",
            "keywords": kws,
            "previous": before,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    added = [k for k in kws if k not in before]
    dropped = [k for k in before if k not in kws]
    return ("🔍 検索語を更新"
            + (f"\n　＋ {' / '.join(added)}" if added else "")
            + (f"\n　− {' / '.join(dropped)}" if dropped else ""))


def _fit(text: str, limit: int) -> str:
    """行の途中で切らずに limit 字へ収める。文の途中で終わると指示文が壊れるため。"""
    if len(text) <= limit:
        return text
    kept: list[str] = []
    for line in text.splitlines():
        if len("\n".join([*kept, line])) > limit:
            break
        kept.append(line)
    return "\n".join(kept) or text[:limit]


def apply_focus(text: str, dry: bool) -> str:
    """重点方針.md を書き換える。write_script.py が次の生成から読む。"""
    text = (text or "").strip()
    if len(text) < 40:
        return ""
    # LLMが自分で見出しを付けてくることがある。こちらの見出しと二重になるので落とす
    text = re.sub(r"\A#+[^\n]*\n+", "", text).strip()
    body = (f"<!-- {date.today():%Y-%m-%d} weekly_review.py が自動生成。"
            "手で直しても次の日曜に上書きされます -->\n"
            f"# 今週の重点方針（{date.today():%Y-%m-%d} 更新）\n\n"
            + _fit(text, MAX_FOCUS_CHARS) + "\n")
    if FOCUS_MD.exists() and FOCUS_MD.read_text(encoding="utf-8") == body:
        return ""
    if not dry:
        FOCUS_MD.write_text(body, encoding="utf-8")
    return "🎯 台本の重点方針を更新（次の生成から効きます）"


def write_proposals(md: str, dry: bool) -> Path | None:
    """ネタ提案の下書き。**採用/却下は社長**なので、ここは反映ではなく提出。"""
    md = (md or "").strip()
    if len(md) < 80:
        return None
    out = PROPOSAL_DIR / f"ネタ提案_{date.today():%Y%m%d}.md"
    if not dry:
        out.write_text(md + "\n", encoding="utf-8")
    return out


def _bullets(items, empty: str = "（なし）") -> list[str]:
    """箇条書き。空なら1行だけプレースホルダを置く。"""
    lines = [f"- {i}" for i in items if str(i).strip()]
    return lines or [f"- {empty}"]


def write_report(res: dict, mats: str, before: dict, dry: bool) -> Path | None:
    """週次レポート。上書き前の値もここに残すので、あとから戻せる。"""
    out = REPORT_DIR / f"週次_{date.today():%Y%m%d}.md"
    body = "\n".join([
        f"# 週次レビュー {datetime.now():%Y-%m-%d %H:%M}",
        "",
        "> weekly_review.py が自動生成。検索語と重点方針は**反映済み**。",
        "> ネタ提案は下書き（採用/却下は社長）。",
        "",
        "## 要点",
        *_bullets(res.get("summary", [])),
        "",
        "## 反映後の検索語",
        *_bullets(res.get("keywords", []), "据え置き"),
        "",
        "## 反映後の重点方針",
        (res.get("focus") or "（据え置き）"),
        "",
        "## 見送り・社長の判断が要ること",
        (res.get("notes") or "（なし）"),
        "",
        "---",
        "## 上書き前の値（戻す時はここから）",
        "### 検索語",
        *_bullets(before.get("keywords", [])),
        "",
        "### 重点方針",
        "```markdown",
        before.get("focus", "（なし）"),
        "```",
        "",
        "---",
        "## この判断に使った材料",
        mats,
        "",
    ])
    if not dry:
        REPORT_DIR.mkdir(exist_ok=True)
        out.write_text(body + "\n", encoding="utf-8")
    return out


# --- 通し -------------------------------------------------------------------
def run(fetch: bool = True, limit: int = FETCH_LIMIT, dry: bool = False) -> dict:
    warnings = []
    if fetch:
        warn = fetch_results(limit)
        if warn:
            warnings.append(warn)
    print("■ 材料を集めています…", file=sys.stderr)
    mats = materials()
    before = {"keywords": current_keywords(),
              "focus": FOCUS_MD.read_text(encoding="utf-8") if FOCUS_MD.exists() else ""}

    print(f"■ 判断（LLM経路: {llm.backend()}）…", file=sys.stderr)
    try:
        res = llm.ask_json(build_prompt(mats), max_tokens=6000)
    except llm.LLMError as e:
        raise ReviewError(f"LLMに繋がりません: {e}") from e

    changes = []
    kws = clean_keywords(res.get("keywords"))
    if res.get("keywords") and not kws:
        warnings.append("⚠️ 検索語の形が変だったので据え置きました")
    change = apply_keywords(kws, dry)
    if change:
        changes.append(change)
    change = apply_focus(res.get("focus", ""), dry)
    if change:
        changes.append(change)

    proposal = write_proposals(res.get("proposals", ""), dry)
    report = write_report(res, mats, before, dry)

    return {
        "ok": True,
        "dry_run": dry,
        "summary": [str(s) for s in (res.get("summary") or [])][:6],
        "changes": changes,
        "notes": (res.get("notes") or "").strip(),
        "warnings": warnings,
        "proposal": str(proposal) if proposal else "",
        "report": str(report) if report else "",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="週次レビュー（分析→検索語・台本方針へ反映）")
    ap.add_argument("--no-fetch", action="store_true",
                    help="実測の取り直し（Chrome）を飛ばす")
    ap.add_argument("--limit", type=int, default=FETCH_LIMIT, help="実測を取り直す本数")
    ap.add_argument("--dry-run", action="store_true", help="反映せず、変わる内容だけ見る")
    args = ap.parse_args()

    try:
        res = run(fetch=not args.no_fetch, limit=args.limit, dry=args.dry_run)
    except ReviewError as e:
        print(json.dumps({"ok": False, "reason": str(e)}, ensure_ascii=False))
        return 3

    for line in res["summary"]:
        print(f"・{line}", file=sys.stderr)
    for line in res["changes"] + res["warnings"]:
        print(line, file=sys.stderr)
    print(json.dumps(res, ensure_ascii=False))   # 最終行 = Bot が読むJSON
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
