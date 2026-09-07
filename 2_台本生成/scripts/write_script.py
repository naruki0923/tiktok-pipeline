#!/usr/bin/env python3
"""2_台本生成 の無人化: 文字起こし → 台本 → 整形 → キャプション を1発で作る。

これまでClaudeとの対話でやっていた台本化を、`台本作成ルール.md` をそのまま
プロンプトに載せて1回のLLM呼び出しに畳んだもの。

    ./.venv/bin/python write_script.py --ref ../文字起こし/ref_027.txt --name 本番_035 \
        --meta '{"url":"https://...","title":"...","views":73500}'

**LLMを叩くのはここ1回だけ**（季節性・重複の判定と、台本と、キャプションを
同じプロンプトで同時に返させる）。検索・数値取得・整形・動画化・投稿は全部
スクリプト側で完結するので、クレジット消費は動画1本あたり1リクエストで済む。

出力:
  2_台本生成/台本_NNN.txt              … 句読点あり原稿（人が読む用・履歴）
  3_動画生成/音声/本番_NNN.txt          … format_script.py 通過後（make_video.py の入力）
  4_投稿/投稿予定/本番_NNN.txt          … YouTube用キャプション
  4_投稿/投稿予定/本番_NNN_TikTok.txt   … TikTok用キャプション（同じ短い形式）
  標準出力にJSON {"ok":..., "title_lines":..., ...} ← Discord bot がこれを読む
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent            # 2_台本生成/scripts
GEN_DIR = HERE.parent                             # 2_台本生成
ROOT = GEN_DIR.parent
RULES = GEN_DIR / "台本作成ルール.md"
SCRIPT_TXT_DIR = ROOT / "3_動画生成" / "音声"
CAPTION_DIR = ROOT / "4_投稿" / "投稿予定"
HISTORY = ROOT / "1_リサーチ" / "採用履歴.tsv"
RESULTS = ROOT / "5_分析" / "実績.tsv"             # 角度×再生数（update_results.py が作る）
WIN_VIEWS = 10_000                                 # これ以上を「勝ち筋」とみなす
JUDGE_AFTER_DAYS = 7                               # 公開これ未満は勝ち負けを判定しない

LINE_PREFIX = "【無料の個別相談はLINEから💬️】"   # キャプション先頭の固定文言
MAX_TITLE_LINES = 3                                # タイトルテロップに統合できる上限
MAX_TELOP_CHARS = 30                               # format_script.py の既定値

sys.path.insert(0, str(HERE))
import llm  # noqa: E402


PROMPT = """あなたは退職給付金ジャンルのTikTok台本ライターです。
参考動画の文字起こしから、下記ルールに沿った台本を作ってください。

# 今日の日付
{today}

# 最優先の事前判定（台本を書く前に必ず確認）
1. **季節性**: この内容は「今日この動画を見る人」に当てはまりますか。
   「◯月に退職すると」「年末調整」「ボーナス前」など、今日の月に当てはまらない
   月依存のネタなら ok=false にしてください。月に依存しない内容（申請手順・減免制度・
   給付金の種類・受給中の働き方など）は ok=true です。
2. **角度の重複**: 判定は**実績で分ける**。下の一覧の再生数を必ず見ること。
   - **【勝ち筋】に載っている角度は、重複を理由に ok=false にしてはいけません。**
     当たった型は繰り返し出すのが正解です。同じ制度・同じ結論でも構いません。
     入口（対象の年齢・期限・きっかけ）や具体例を変えて作り直してください。
     むしろ参考動画が勝ち筋と同じ領域なら、それは**採用すべき理由**です。
   - 【沈んだ角度】と実質同じ切り口なら ok=false（伸びなかった型を繰り返さない）。
   - どちらにも無い新しい角度は ok=true。
3. **完コピの成立**: 文字起こしが短すぎる・内容が薄い・音楽だけ等で台本にできないなら ok=false。

ok=false のときは reason に理由だけ書き、script/title/tags は空文字・空配列にしてください。

# 当チャンネルの実績（角度 × 再生数）
{angles}

# 台本作成ルール（これが唯一の正。すべて守ること）
{rules}

# 参考動画の情報
{meta}

# 参考動画の文字起こし（誤変換を含む下書き。数字・同音異義語は文脈で直すこと）
{transcript}

# 書式（ルール本文の見た目に引きずられないこと）
`台本作成ルール.md` はフックや固定文言を**改行済みのブロック**で例示していますが、
あれは完成イメージであって出力形式ではありません。**script は句読点のある普通の文章**で
書いてください。**文の途中で改行しない**こと。テロップを割りたい位置には改行ではなく
**読点「、」**を打ちます。固定文言も1つの文としてつなげて書いてください。

- 悪い例（改行で割っている）:
  `退職する人は全員\\n知っておくべき\\n退職給付金の制度があります`
- 良い例（読点で割る）:
  `退職する人は全員、知っておくべき、退職給付金の制度があります。`

- **冒頭1文は全体で2〜3フレーズ（読点は1〜2個）にすること。**
  4フレーズ以上はタイトルカードに収まらないため不可です。

# 字数（最重要・ここを外すとやり直しになる）
script は**空白を除いて450〜500字**に収めてください（＝65〜72秒）。520字を超えたら失格です。
参考動画が「7選」「5選」でも、**項目を3つに絞って**その3つを厚く書いてください。
1項目あたりの説明を薄めて数を維持するのは逆効果です。フック・いいね誘導・中盤フォロー誘導・
CTAの固定文言はこの字数に含めたうえで必ず入れること。

# 出力形式（JSONのみ）
{{
  "ok": true または false,
  "reason": "判定の理由を1〜2文",
  "angle": "この台本の切り口を30字程度で（採用履歴に残す）",
  "script": "台本の本文。句読点あり・普通の文章。見出しや説明は入れない。改行は\\nで表す",
  "title": "動画タイトル。20〜30字。【】は付けない",
  "tags": ["退職", "失業保険", "..."]
}}
"""


# --- 補助 -----------------------------------------------------------------
def covered_angles(exclude_ref: Path | None = None, limit: int = 18) -> str:
    """自分の投稿の「角度 × 再生数」。重複判定の材料。

    2026-09-04 に作り直した。以前は 採用履歴.tsv のメモ欄を渡していたが、自動運用では
    採用行のメモが空で、メモが埋まるのは pipeline.mark_rejected が書く【不採用】理由だけ。
    つまり **一度も扱っていない角度の一覧を「既に扱った角度」として渡していた**。
    その結果、当たった角度（国保・住民税の減免＝032で243,000再生）が提案されるたびに
    「重複」で落ち続け、8/17〜9/3で9本連続の不採用になっていた。

    いまは 5_分析/実績.tsv（update_results.py が作る）を読み、再生数で
    【勝ち筋】と【沈んだ角度】に分けて渡す。勝ち筋は重複を理由に落とさせない。
    """
    if not RESULTS.exists():
        return "（実績データなし。5_分析/scripts/update_results.py を実行してください）"
    won, lost, fresh = [], [], []
    today = date.today()
    for line in RESULTS.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        p = line.split("\t")
        if len(p) < 5 or not p[2].isdigit():
            continue
        views = int(p[2])
        row = f"- {views:,}再生 {p[3][:60]}"
        # 再生は投稿後1ヶ月伸び続ける。若い動画を「沈んだ」に入れると、
        # まだ伸びる余地のある角度を禁止してしまう（2026-09-04）。
        try:
            age = (today - date.fromisoformat(p[1])).days
        except ValueError:
            age = 999
        if views >= WIN_VIEWS:
            won.append((views, row))
        elif age < JUDGE_AFTER_DAYS:
            fresh.append((views, f"- {views:,}再生 公開{age}日 {p[3][:60]}"))
        else:
            lost.append((views, row))
    for bucket in (won, lost, fresh):
        bucket.sort(reverse=True)
    out = ["## 【勝ち筋】伸びた角度（重複を理由に落とさないこと。むしろ再訪する）"]
    out += [r for _, r in won[:limit]] or ["- （まだ無し）"]
    out.append("")
    out.append(f"## 【沈んだ角度】{WIN_VIEWS:,}再生未満（これと同じ切り口なら ok=false）")
    out += [r for _, r in lost[:limit]] or ["- （まだ無し）"]
    out.append("")
    out.append(f"## 【判定中】公開{JUDGE_AFTER_DAYS}日未満（勝ち負けは未確定。"
               "直後の焼き直しは避けるが、沈んだ扱いにはしないこと）")
    out += [r for _, r in fresh[:limit]] or ["- （まだ無し）"]
    return "\n".join(out)


def next_name() -> str:
    """未使用の 本番_NNN を決める（音声/ の最大値+1）。"""
    nums = [int(p.stem.split("_")[1]) for p in SCRIPT_TXT_DIR.glob("本番_*.txt")
            if p.stem.split("_")[-1].isdigit()]
    return f"本番_{(max(nums) + 1) if nums else 1:03d}"


_SENT_END = ("。", "！", "？", "!", "?")
_SOFT_END = _SENT_END + ("、", "，")
# フックの2文目（保存煽り）の書き出し。台本作成ルール.md の定型なので決め打ちできる。
_HOOK_TEASE = ("特に", "知っておかないと")
_INLINE_HOOK_TEASE_RE = re.compile(r"[、，]\s*(?=特に)")


def _cap_title_phrases(text: str) -> str:
    """冒頭1文の短い列挙を、内容を落とさず3フレーズ以内へまとめる。

    読点は format_script.py で1行ずつに分かれる。LLMが4個以上の短い
    列挙を返した場合は、最短の隣接ペアを中黒でつないで1行にまとめる。
    30字を超える結合は自動改行で再び4行になるため行わず、動画直前の
    title_problems() に安全停止を任せる。
    """
    match = re.match(r"(?s)(\s*)([^。．！？!?]+)([。．！？!?])(.*)", text)
    if not match:
        return text
    leading, first, sentence_end, rest = match.groups()
    phrases = [part.strip() for part in re.split(r"[、，]", first)]
    if any(not phrase for phrase in phrases):
        return text

    while len(phrases) > MAX_TITLE_LINES:
        candidates = []
        for i in range(len(phrases) - 1):
            joined_len = len(phrases[i]) + 1 + len(phrases[i + 1])
            if joined_len <= MAX_TELOP_CHARS:
                candidates.append((joined_len, i))
        if not candidates:
            break
        _, i = min(candidates)
        phrases[i:i + 2] = [f"{phrases[i]}・{phrases[i + 1]}"]

    return f"{leading}{'、'.join(phrases)}{sentence_end}{rest}"


def normalize_raw(script: str) -> str:
    """LLMが「改行済みの行」で返してきた台本を、句読点のある普通の文章に直す。

    `台本作成ルール.md` はフック・いいね誘導・中盤フォロー・CTAの固定文言を
    **改行済みのブロック**として例示しているため、LLMがその見た目を真似て
    句読点なしの行を返すことがある（本番_036で発生）。

    ⚠️ format_script.split_sentences は **改行も文の区切りとして扱う**ので、
    読点を足すだけでは足りず、1文になるべき行は**結合**しないといけない。
    そのまま渡すと1行＝1文と解釈され、フックがバラけて
    タイトルカードが1行だけになる（＝本番_036で起きた不具合）。

    LLMが入れた改行は「ここでテロップを割る」意図なので読点に変換する。
    句点で終わっている行はそこで文を閉じる。既に普通の文章なら素通りする。
    """
    out: list[str] = []
    for block_index, block in enumerate(re.split(r"\n\s*\n", script.strip())):
        # プロンプトどおりの「改行なしの普通の文章」でも、保存煽りは2文目にする。
        # 従来は行頭だけを見ていたため「…3つ、特に最後の…」を分離できず、
        # 保存煽りまでタイトルカードに入り6行になることがあった（本番_057）。
        if block_index == 0:
            block = _INLINE_HOOK_TEASE_RE.sub("。\n", block, count=1)
        buf = ""
        for ln in (l.strip() for l in block.splitlines()):
            if not ln:
                continue
            # 「特に最後の3つ目は…」は保存煽り＝フックの2文目。ここで文を切らないと
            # 1文目と繋がってタイトルカードに混ざる（[[title-lines-cap-at-3]] の指摘）。
            if buf and ln.startswith(_HOOK_TEASE):
                out.append(buf if buf.endswith(_SENT_END) else buf.rstrip("、，") + "。")
                buf = ""
            if buf and not buf.endswith(_SOFT_END):
                buf += "、"
            buf += ln
            if ln.endswith(_SENT_END):
                out.append(buf)
                buf = ""
        if buf:
            out.append(buf if buf.endswith(_SENT_END) else buf.rstrip("、，") + "。")
        out.append("")
    return _cap_title_phrases("\n".join(out).strip()) + "\n"


def title_lines_of(formatted: str) -> int:
    """--title-lines に渡す行数＝整形後の「最初の1文」のフレーズ行数（上限3）。

    format_script.py は句点を空行に変換するので、先頭の空行までが1文目にあたる。
    2文目以降は絶対に含めない（混ぜると社長の指摘対象になる）。
    """
    block: list[str] = []
    for ln in formatted.splitlines():
        if not ln.strip():
            break
        block.append(ln)
    return min(max(len(block), 1), MAX_TITLE_LINES)


def build_caption(title: str, tags: list[str]) -> str:
    """TikTok/YouTube 共通のキャプション1行。固定文言＋タイトル＋ハッシュタグ。"""
    title = title.strip().strip("【】")
    tag_s = " ".join("#" + t.lstrip("#").strip() for t in tags if t.strip())
    return f"{LINE_PREFIX}{title} {tag_s}".strip()


SHRINK_PROMPT = """次のTikTok台本が長すぎます。推定{est}秒ですが、上限は75秒（450〜500字）です。

# 直し方（これ以外はしない）
- **項目数を減らして**削る（7選→3選など）。残した項目の説明は薄めない。
- フック・「この動画をもう二度と／あなたのおすすめには表示されませんので／忘れないよう、
  いいねと保存して」・中盤フォロー誘導・末尾CTAの固定文言は**そのまま残す**。
- 敬体（です/ます）と事実ベースを維持。断定・誇大表現は使わない。
- タイトルは項目数を減らした後の内容に合わせて直す。

# 今の台本
{script}

# 出力形式（JSONのみ）
{{"script": "短くした台本（句読点あり・改行は\\nで表す）", "title": "タイトル（【】なし）", "tags": ["..."]}}
"""

_EST_RE = re.compile(r"推定\s*約([\d.]+)秒")
HARD_LIMIT_SECONDS = 75


def est_seconds(log: str) -> float:
    """format_script.py のサマリから推定尺を読む。読めなければ0（＝判定しない）。"""
    m = _EST_RE.search(log)
    return float(m.group(1)) if m else 0.0


def run_format(raw_path: Path, out_path: Path) -> tuple[str, str]:
    """format_script.py に通す。(整形後テキスト, 警告を含むログ) を返す。"""
    p = subprocess.run(
        [sys.executable, str(HERE / "format_script.py"), str(raw_path), "-o", str(out_path)],
        capture_output=True, text=True, timeout=180,
    )
    if p.returncode != 0:
        raise RuntimeError(f"format_script.py が失敗:\n{p.stderr[-800:]}")
    return out_path.read_text(encoding="utf-8"), p.stderr


def main() -> None:
    ap = argparse.ArgumentParser(description="文字起こしから台本・キャプションを自動生成する")
    ap.add_argument("--ref", required=True, help="文字起こしファイル（ref_NNN.txt）")
    ap.add_argument("--name", default=None, help="出力名（既定: 音声/ の次の連番）")
    ap.add_argument("--meta", default="{}", help="参考動画のメタ情報JSON（URL・再生数など）")
    args = ap.parse_args()

    ref = Path(args.ref)
    if not ref.exists():
        sys.exit(f"[エラー] 文字起こしが見つかりません: {ref}")
    transcript = ref.read_text(encoding="utf-8").strip()
    if len(transcript) < 200:
        print(json.dumps({"ok": False, "reason": f"文字起こしが短すぎます（{len(transcript)}字）"},
                         ensure_ascii=False))
        sys.exit(3)

    name = args.name or next_name()
    print(f"■ {name} / LLM経路: {llm.backend()}", file=sys.stderr)

    prompt = PROMPT.format(
        today=f"{date.today():%Y年%m月%d日}",
        angles=covered_angles(exclude_ref=ref),
        rules=RULES.read_text(encoding="utf-8"),
        meta=args.meta,
        transcript=transcript[:8000],
    )
    res = llm.ask_json(prompt, max_tokens=6000)

    if not res.get("ok"):
        reason = res.get("reason", "理由不明")
        print(f"✗ 不採用: {reason}", file=sys.stderr)
        print(json.dumps({"ok": False, "reason": reason, "name": name}, ensure_ascii=False))
        sys.exit(3)   # 呼び出し側が「次の候補へ」と判断できるように

    script = (res.get("script") or "").strip()
    if len(script) < 200:
        print(json.dumps({"ok": False, "reason": f"台本が短すぎます（{len(script)}字）"},
                         ensure_ascii=False))
        sys.exit(3)

    num = name.split("_")[-1]
    raw_path = GEN_DIR / f"台本_{num}.txt"
    raw_path.write_text(normalize_raw(script) + "\n", encoding="utf-8")

    out_path = SCRIPT_TXT_DIR / f"{name}.txt"
    formatted, log = run_format(raw_path, out_path)
    print(log, file=sys.stderr)

    # 尺オーバーのときだけ、短いプロンプトで1回だけ詰め直す（LLM呼び出しは最大2回）。
    est = est_seconds(log)
    if est > HARD_LIMIT_SECONDS:
        print(f"⟳ {est:.0f}秒で上限超過。項目を減らして詰め直します（1回だけ）", file=sys.stderr)
        try:
            fix = llm.ask_json(SHRINK_PROMPT.format(est=int(est), script=script), max_tokens=4000)
            shorter = (fix.get("script") or "").strip()
            if len(shorter) >= 200:
                script = shorter
                res["title"] = fix.get("title") or res.get("title", "")
                res["tags"] = fix.get("tags") or res.get("tags") or []
                raw_path.write_text(normalize_raw(script) + "\n", encoding="utf-8")
                formatted, log = run_format(raw_path, out_path)
                print(log, file=sys.stderr)
        except llm.LLMError as e:
            print(f"⚠ 詰め直しに失敗（長いまま進みます）: {e}", file=sys.stderr)

    caption = build_caption(res.get("title", ""), res.get("tags") or [])
    CAPTION_DIR.mkdir(parents=True, exist_ok=True)
    (CAPTION_DIR / f"{name}.txt").write_text(caption + "\n", encoding="utf-8")
    (CAPTION_DIR / f"{name}_TikTok.txt").write_text(caption + "\n", encoding="utf-8")

    out = {
        "ok": True,
        "name": name,
        "angle": res.get("angle", ""),
        "title": res.get("title", ""),
        "title_lines": title_lines_of(formatted),
        "script_txt": str(out_path),
        "raw_txt": str(raw_path),
        "caption": caption,
        "warnings": [w.strip("- ").strip() for w in log.splitlines()
                     if w.strip().startswith("-")],
    }
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
