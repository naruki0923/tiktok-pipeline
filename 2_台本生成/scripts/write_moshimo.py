#!/usr/bin/env python3
"""もしも型3D動画の台本＋場面指定を、文字起こしから1発で作る（毎朝の自動生成の本線）。

    ./.venv/bin/python write_moshimo.py --ref ../文字起こし/ref_027.txt --meta '{"url":"..."}'
    ./.venv/bin/python write_moshimo.py --revise 本番_150 --instruction "フックをもっと短く"

型は「悪いもしも」（2026-11-19・issue #30）: もし〇〇だったら？ → 悪い未来 → 終盤で今の制度 → 固定CTA。
リサーチで選んだ参考動画は「今伸びている悩み」と「制度の事実」の出どころで、台本は完コピしない。
その制度につながるタイトル案を `プロンプト_もしもタイトル.md`（社長が手応えを見たプロンプト）の条件で
3〜5本出させ、いちばん良い1本で台本にする（同じ1回の呼び出しの中で）。
悪いもしもは自由に描かせ、今の制度の事実は文字起こしにあるものだけで書かせる。
ルールは `2_台本生成/もしも型ルール.md`（人が書く）、場面は `3_動画生成/3D/shots.py` の型から選ばせる。

**LLMは1回、直しが要る時だけもう1回**（形の崩れ・尺の外れをまとめて1回で直させる）。

出力（write_script.py と同じ置き場・同じ名前。旧形式で作り直す時もそのまま使える）:
  2_台本生成/台本_NNN.txt              … 句読点あり原稿
  3_動画生成/音声/本番_NNN.txt          … format_script.py 通過後（旧形式の make_video.py の入力）
  3_動画生成/音声/本番_NNN.3d.json      … 3D用 spec（make_3d.py の入力）。LLMの元JSONも source に残す
  4_投稿/投稿予定/本番_NNN.txt / _TikTok.txt … キャプション
  標準出力の最後の行にJSON {"ok":..., "style":"3d", ...} ← Discord bot がこれを読む
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent            # 2_台本生成/scripts
GEN_DIR = HERE.parent
ROOT = GEN_DIR.parent
RULES = GEN_DIR / "もしも型ルール.md"
TITLE_RULES = GEN_DIR / "プロンプト_もしもタイトル.md"
SCRIPT_TXT_DIR = ROOT / "3_動画生成" / "音声"
CAPTION_DIR = ROOT / "4_投稿" / "投稿予定"
THREE_D = ROOT / "3_動画生成" / "3D"
EXAMPLE = THREE_D / "examples" / "byoki_src.json"     # 悪いもしもの形の見本（内容は真似させない）

sys.path.insert(0, str(HERE))
sys.path.insert(0, str(THREE_D))
sys.path.insert(0, str(ROOT / "3_動画生成" / "scripts"))
import llm  # noqa: E402
import shots  # noqa: E402
import write_script as ws  # noqa: E402  補助（実績・重点方針・連番・キャプション・整形）を共有する
from target_age import off_target  # noqa: E402

MIN_SEC, MAX_SEC = 61.0, 75.0     # ナレーションの見積もり（動画はこれ＋約2秒）。必ず1分超え

PROMPT = """あなたは退職給付金ジャンルのTikTok台本ライターです。
リサーチで選んだ、今伸びている参考動画の文字起こしを材料に、**悪いもしも型**の3D動画の台本と場面指定を作ってください。
参考動画の完コピではありません。文字起こしから「視聴者が抱えている不安」と「つなげる制度の事実」だけを借り、
タイトルは下の「タイトルの条件」で、構成・言い回しは「もしも型ルール」で一から書きます。

# 手順
1. 文字起こしに出てくる制度と、それを必要とする人の不安を読み取る。
2. その制度につながる「もし〇〇だったら？」のタイトル案を、タイトルの条件に沿って3〜5本考える
   （異なる状況を選ぶ。制度とのつながりが弱い案は入れない）。
3. いちばん状況が目に浮かび、文字起こしの事実で終盤の制度パートまで書ける1本を選ぶ。
4. 選んだタイトルで台本を書く。

# 今日の日付
{today}

# 最優先の事前判定（書く前に必ず確認。どれか1つでも当たれば ok=false）
0. **対象年齢**: 65歳以上・65歳前後で退職する人向けの内容（65歳の誕生日前の退職、失業手当と年金の同時受給、
   64歳で年金を申請すると支給停止になる併給調整、高年齢求職者給付金、65歳からの介護保険料など）は ok=false。
   商材の対象は20〜64歳のため。**再生が伸びた角度でも例外にしない。**64歳以下向けに作り替えられるなら ok=true。
1. **季節性**: 今日この動画を見る人に当てはまるか。今日の月に当てはまらない月依存のネタなら ok=false。
2. **角度の重複**: 実績で分ける。【勝ち筋】の角度は重複を理由に落とさない（入口を変えて再訪する）。
   【沈んだ角度】と実質同じ切り口なら ok=false。どちらにも無い新しい角度は ok=true。
3. **悪いもしも型にできるか**: 自分にも起こりそうな悪い未来と、文字起こしの事実でつなげる制度の両方が書けるか。
   文字起こしに制度の事実（対象・窓口・申請など）がほとんど無い、自宅・公園・役所の3か所で描けない、なら ok=false。

ok=false のときは reason に理由だけ書き、他は空でよい。

# 当チャンネルの実績（角度 × 再生数）
{angles}

# 今週の重点方針（5_分析 の実測から自動更新）
{focus}

# 最近の3D動画で使ったタイトル（同じタイトル・ほぼ同じ言い換えは選ばない。同じ制度でも別の状況から入る）
{recent}

# タイトルの条件（これに沿ってタイトル案を出す）
{title_rules}

# もしも型ルール（これが唯一の正。すべて守ること）
{rules}

# カウンターの前提（固定）
{assumption}

# 選べるショットの型（shot にはこの名前だけを書く。座標は書かない）
{shot_guide}

# 場面の小物に書く文字（props。話題に合わせて差し替える。1行10字まで）
- letter_real: 自宅のテーブルの書類 2行（「給与明細」「家賃の督促」「納付通知書」など）
- pc_real: ノートPCの画面 2行（求人・検索画面など）
- office_sign: 役所の壁の大看板（「市民窓口」「ハローワーク」など）
- office_window: 主人公が行く窓口の吊り看板（「国民健康保険」「雇用保険」など）
- office_notice: 窓口のカウンターに置いた通知書・申請書 2行
- office_doc: 窓口に持っていく書類（「受給資格者証」「離職票」など）

# 参考動画の情報
{meta}

# 参考動画の文字起こし（誤変換を含む下書き。数字・同音異義語は文脈で直すこと）
{transcript}

# 出力の例（病気の回。形だけ真似る。話題・制度名・制度の説明の言い回しは真似ない。事実は必ず上の文字起こしから）
{example}

# 出力形式（JSONのみ）
{{
  "ok": true または false,
  "reason": "判定の理由と、その案を選んだ理由を1〜2文",
  "angle": "この台本の切り口を30字程度で",
  "title_candidates": [
    {{"title": "もし〇〇だったら？", "story": "描く悪いもしもの展開を1文", "system": "つなげる制度名", "close": "誤解のない短い締めの一言"}}
  ],
  "title_band": "画面上の固定タイトル＝選んだタイトル。\\nで区切った2行・1行14字まで",
  "title": "投稿のタイトル。選んだタイトル＋ひとこと。20〜30字。【】は付けない。制度名は入れない",
  "day_label": "カウンターの見出し。8字まで（「退職してから」「休み始めて」など）",
  "tags": ["退職", "..."],
  "props": {{...上の6項目...}},
  "segments": [
    {{"part": "hook", "say": ["行", "行"], "shot": "home_wide", "big": "大字幕"}},
    {{"part": "real", "say": [...], "shot": "...", "big": "...", "sub": "小字幕（省略可）", "day": 1, "money": 120, "join": false}},
    {{"part": "system", "say": [...], "shot": "office_walk", "big": "...", "sub": "..."}}
  ],
  "facts": ["今の制度で使った事実 ← 文字起こしのどの言い回しか"]
}}
- title_candidates は3〜5本。選んだ案を先頭に置く。
- パートは hook → real（悪いもしも）→ system（今の制度）の順に1回ずつ。ideal は使わない。
- say は読み上げる行。1行30字まで・句読点なし。1区間に1〜4行。
- join: true は「この区間の最後の行と次の区間の頭で1文になる」時だけ（同じパートの中だけ）。
- CTAは**書かない**（自動で足す）。
"""

FIX_PROMPT = """次のもしも型台本JSONに直すべき点があります。**直すべき点だけ**直して、同じ形のJSONを全部返してください。
内容（話題・事実・流れ）は変えないこと。新しい制度の事実を足さないこと（事実は今のJSONにあるものだけ）。

# 直すべき点
{errors}
{kind_note}
# もしも型ルール（抜粋ではなく全文）
{rules}

# 選べるショットの型
{shot_guide}

# 今のJSON
{current}
"""

REVISE_PROMPT = """次のもしも型台本JSONを、社長の指示どおりに手直ししてください。同じ形のJSONを全部返します。
指示に無い所は変えないこと。事実は元の台本にあるものだけ（新しい制度の事実を足さない）。
手直しの内容は "note" に1文で書いてください。

# 社長の指示
{instruction}
{kind_note}
# もしも型ルール
{rules}

# 選べるショットの型
{shot_guide}

# 今のJSON
{current}
"""


# 理想のもしも（本番_147/148）を直す時だけ添える。ルールは悪いもしも型で書いてあるので、
# そのまま渡すと LLM が並びを組み替えてしまい、ideal 型の検査で落ちる
IDEAL_NOTE = """
# この回の型（ルールより優先）
この回は以前の「理想のもしも」型で作った回です。パートの並びは hook → ideal（もしもの世界）→ real（現実）→ system のまま変えないこと。
下のルールの「型」「悪いもしも」の説明はこの回には当てはまりません（巻き戻しとCTAは自動で入る）。
"""


def kind_note(kind: str) -> str:
    return IDEAL_NOTE if kind == "ideal" else ""


# ------------------------------------------------------------------ 検査
def _digits(text: str) -> set[str]:
    return set(re.findall(r"\d+", unicodedata.normalize("NFKC", text)))


def fact_warnings(spec: dict, transcript: str) -> list[str]:
    """現実・今の制度で使った数字が文字起こしに無ければ警告（社長の確認ポイントとして出す）。

    文字起こしは漢数字・誤変換が混ざるので、落とさずに警告だけにする。
    日付カウンター用の「1か月後」「3か月後」等の時間の言い回しは除く。
    """
    have = _digits(transcript)
    # 悪いもしもの real は仮の話（数字はイメージ）なので、事実として見るのは今の制度だけ
    parts = ("system",) if spec.get("kind") == "bad" else ("real", "system")
    out = []
    for s in spec["segments"]:
        if s.get("part") not in parts:
            continue
        text = re.sub(r"\d+\s*(か月|ヶ月|カ月|日目|年後)", "", unicodedata.normalize("NFKC", " ".join(s["say"])))
        miss = sorted(d for d in _digits(text) if d not in have)
        if miss:
            out.append(f"{'今の制度' if s['part'] == 'system' else '現実'}の数字「{'・'.join(miss)}」が文字起こしに見当たりません（要確認）: {' '.join(s['say'])}")
    return out


def measure(spec: dict) -> tuple[float, str]:
    """ナレーションの長さ。VOICEVOXが動いていれば実測に近い値、無ければ文字数から。"""
    try:
        import voicevox_tts as vv
        sid, *_ = vv.find_speaker_id(vv.DEFAULT_HOST, "青山龍星", "ノーマル")
        return shots.narration_seconds(spec, shots.voicevox_line_seconds(vv.DEFAULT_HOST, sid)), "VOICEVOX"
    except Exception:  # noqa: BLE001 - VOICEVOXが止まっていても台本は作る
        return shots.narration_seconds(spec), "文字数"


def _spoken(src: dict) -> list[str]:
    """LLMの元JSONの say を全部（形が崩れていても落ちない。崩れは build_spec が直させる）。"""
    segs = src.get("segments")
    out = []
    for sg in segs if isinstance(segs, list) else []:
        if isinstance(sg, dict):
            say = sg.get("say") or []
            out += [say] if isinstance(say, str) else [str(x) for x in say]
    return out


def check(src: dict, name: str, kind: str = "bad") -> tuple[dict, list[str], list[str]]:
    """形・尺・65歳系を全部見る。(spec, エラー, 警告)。新しく作る回は必ず悪いもしも（kind="bad"）。"""
    spec, errors, warns = shots.build_spec(src, name, kind=kind)
    hit = off_target("\n".join([src.get("title_band") or "", src.get("title") or "",
                                *(str(t) for t in src.get("tags") or []),
                                *_spoken(src)]))
    if hit:
        return {}, [f"__OFF_TARGET__65歳系の内容（「{hit}」）は商材の対象外"], warns
    if errors:
        return spec, errors, warns
    sec, how = measure(spec)
    spec["est_seconds"] = round(sec, 1)
    if sec < MIN_SEC:
        chars = sum(len(x) for x in shots.spoken_lines(spec))
        errors.append(f"尺が短すぎます（ナレーション約{sec:.0f}秒・{how}で計測）。必ず1分を超えるよう、"
                      f"{'もしもの世界か現実' if kind == 'ideal' else '悪いもしも'}の区間を足して、say を全部で今より{int((MIN_SEC + 3 - sec) * 6.5)}字ほど増やしてください（今{chars}字）")
    elif sec > MAX_SEC:
        errors.append(f"尺が長すぎます（ナレーション約{sec:.0f}秒・{how}で計測）。上限は{MAX_SEC:.0f}秒です。"
                      f"say を全部で{int((sec - 68) * 6.5)}字ほど減らしてください")
    return spec, errors, warns


def ask_until_ok(first: dict, name: str, fixer, kind: str = "bad") -> tuple[dict, dict, list[str]]:
    """検査 → ダメなら1回だけ直させる。(src, spec, 警告)。直らなければ ValueError。"""
    src = first
    spec, errors, warns = check(src, name, kind)
    if errors and not errors[0].startswith("__OFF_TARGET__"):
        print("⟳ 直しが要る点:\n" + "\n".join(f"  - {e}" for e in errors), file=sys.stderr)
        fixed = fixer(src, errors)
        if fixed:
            src = {**first, **fixed, "ok": True}
            spec, errors, warns = check(src, name, kind)
    if errors:
        raise ValueError(" / ".join(e.replace("__OFF_TARGET__", "") for e in errors)[:600])
    return src, spec, warns


def _fixer(rules: str, kind: str = "bad"):
    def fix(src: dict, errors: list[str]) -> dict | None:
        try:
            return llm.ask_json(FIX_PROMPT.format(
                errors="\n".join(f"- {e}" for e in errors), kind_note=kind_note(kind),
                rules=rules, shot_guide=shots.shot_guide(kind),
                current=json.dumps(src, ensure_ascii=False, indent=1)), max_tokens=8000)
        except llm.LLMError as e:
            print(f"⚠ 直しの呼び出しに失敗: {e}", file=sys.stderr)
            return None
    return fix


# ------------------------------------------------------------------ 書き出し
def spec_path(name: str) -> Path:
    return SCRIPT_TXT_DIR / f"{name}.3d.json"


def _as_list(v) -> list[str]:
    """LLMが配列のはずの所を文字列で返した時に、1文字ずつに割らないための受け口。"""
    if isinstance(v, str):
        return [x for x in re.split(r"[\s、,]+", v) if x]
    return [str(x) for x in (v or []) if str(x).strip()]


def save(name: str, src: dict, spec: dict, warns: list[str]) -> dict:
    src = {**src, "tags": _as_list(src.get("tags")), "facts": _as_list(src.get("facts"))
           if not isinstance(src.get("facts"), str) else [src["facts"]]}
    num = name.split("_")[-1]
    raw_path = GEN_DIR / f"台本_{num}.txt"
    raw_path.write_text(shots.script_text(spec), encoding="utf-8")
    out_path = SCRIPT_TXT_DIR / f"{name}.txt"
    formatted, log = ws.run_format(raw_path, out_path)
    spec = {**spec, "source": src, "facts": src.get("facts") or [], "created": date.today().isoformat()}
    spec_path(name).write_text(json.dumps(spec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    caption = ws.build_caption(src.get("title", ""), src.get("tags") or [])
    CAPTION_DIR.mkdir(parents=True, exist_ok=True)
    (CAPTION_DIR / f"{name}.txt").write_text(caption + "\n", encoding="utf-8")
    (CAPTION_DIR / f"{name}_TikTok.txt").write_text(caption + "\n", encoding="utf-8")
    # format_script の検査は旧形式向け（いいね・中盤フォローが無いと警告する）。もしも型では拾わない
    skip = ("いいね", "保存", "中盤", "フォロー誘導")
    fwarn = [w.strip("- ").strip() for w in log.splitlines()
             if w.strip().startswith("-") and not any(k in w for k in skip)]
    return {
        "ok": True, "style": "3d", "name": name,
        "angle": src.get("angle", ""), "title": src.get("title", ""),
        "title_band": spec["title"], "title_lines": ws.title_lines_of(formatted),
        "est_seconds": spec.get("est_seconds"),
        "script_txt": str(out_path), "spec": str(spec_path(name)), "raw_txt": str(raw_path),
        "caption": caption, "facts": spec["facts"], "warnings": warns + fwarn,
        "title_candidates": candidates(src),
    }


def candidates(src: dict) -> list[dict]:
    """タイトル案（LLMの形が崩れていても落ちない）。Discordに「他の案」として出す。

    65歳系の案は出さない（選ばなかった案でも、社長が「その案で直して」と頼む入口になるため）。
    """
    raw = src.get("title_candidates")
    out = []
    for c in raw if isinstance(raw, list) else []:
        if isinstance(c, dict) and str(c.get("title") or "").strip():
            row = {k: str(c.get(k) or "").strip() for k in ("title", "story", "system", "close")}
        elif isinstance(c, str) and c.strip():
            row = {"title": c.strip(), "story": "", "system": "", "close": ""}
        else:
            continue
        if not off_target("\n".join(row.values())):
            out.append(row)
    return out[:5]


RECENT_TITLES = 20


def recent_titles(limit: int = RECENT_TITLES, exclude: str | None = None) -> str:
    """最近の3Dの回のタイトル帯（新しい順）。日をまたいで同じタイトルを選ばせないため。

    実績.tsv は投稿して数字が付いた回の角度しか持たないので、公開前・未投稿の回はここで拾う。
    spec は30日の自動削除の対象外（消えるのは mp4/wav/mp3 だけ）。
    """
    def num(p: Path) -> int:
        m = re.search(r"_(\d+)\.3d\.json$", p.name)
        return int(m.group(1)) if m else -1

    out = []
    for p in sorted(SCRIPT_TXT_DIR.glob("本番_*.3d.json"), key=num, reverse=True):
        if exclude and p.name == f"{exclude}.3d.json":
            continue
        try:
            title = str(json.loads(p.read_text(encoding="utf-8")).get("title") or "")
        except (OSError, ValueError, AttributeError):
            continue
        title = re.sub(r"\s", "", title)
        if title and title not in out:
            out.append(title)
        if len(out) >= limit:
            break
    return "\n".join(f"- {t}" for t in out) or "（まだ無し）"


def title_rules() -> str:
    """プロンプト_もしもタイトル.md の「## タイトルの条件」以下（前置きは人向けの説明なので渡さない）。"""
    try:
        text = TITLE_RULES.read_text(encoding="utf-8")
    except OSError:
        return "（タイトルの条件ファイルがありません。もしも型ルールに従う）"
    m = re.search(r"^## タイトルの条件", text, re.M)
    return (text[m.start():] if m else text).strip()


def fail(reason: str, name: str) -> None:
    print(f"✗ 不採用: {reason}", file=sys.stderr)
    print(json.dumps({"ok": False, "reason": reason, "name": name}, ensure_ascii=False))
    sys.exit(3)


def example_json() -> str:
    try:
        return json.dumps(json.loads(EXAMPLE.read_text(encoding="utf-8")), ensure_ascii=False)
    except (OSError, ValueError):
        return "（例なし）"


def main() -> None:
    ap = argparse.ArgumentParser(description="もしも型3D動画の台本＋場面指定を作る")
    ap.add_argument("--ref", help="文字起こしファイル（ref_NNN.txt）")
    ap.add_argument("--name", default=None, help="出力名（既定: 音声/ の次の連番）")
    ap.add_argument("--meta", default="{}", help="参考動画のメタ情報JSON")
    ap.add_argument("--revise", default=None, help="手直しする回（本番_NNN）")
    ap.add_argument("--instruction", default="", help="--revise の指示")
    args = ap.parse_args()
    rules = RULES.read_text(encoding="utf-8")

    if args.revise:
        name = args.revise
        if not spec_path(name).exists():
            fail(f"{name} は3D（もしも型）の回ではありません", name)
        old = json.loads(spec_path(name).read_text(encoding="utf-8"))
        current = old.get("source") or {}
        if not current.get("segments"):
            # 手で置いた spec（試作から移した回など）には LLM の元JSONが無く、直す土台が無い
            fail(f"{name} は手で作った3Dの回なので、自動では直せません（spec を直接直してください）", name)
        print(f"■ {name} を手直し / LLM経路: {llm.backend()}", file=sys.stderr)
        kind = shots.kind_of(current)        # 理想のもしもで作った回（147・148）は、その型のまま直す
        res = llm.ask_json(REVISE_PROMPT.format(
            instruction=args.instruction, kind_note=kind_note(kind), rules=rules,
            shot_guide=shots.shot_guide(kind),
            current=json.dumps(current, ensure_ascii=False, indent=1)), max_tokens=8000)
        note = res.pop("note", "")
        try:
            src, spec, warns = ask_until_ok({**current, **res, "ok": True}, name, _fixer(rules, kind), kind)
        except ValueError as e:
            fail(f"手直し後の台本が使えません: {e}", name)
        out = save(name, src, spec, warns)
        out["note"] = note
        print(json.dumps(out, ensure_ascii=False))
        return

    if not args.ref:
        ap.error("--ref か --revise を指定してください")
    ref = Path(args.ref)
    if not ref.exists():
        sys.exit(f"[エラー] 文字起こしが見つかりません: {ref}")
    transcript = ref.read_text(encoding="utf-8").strip()
    name = args.name or ws.next_name()
    if len(transcript) < 200:
        fail(f"文字起こしが短すぎます（{len(transcript)}字）", name)
    print(f"■ {name}（もしも型3D）/ LLM経路: {llm.backend()}", file=sys.stderr)

    prompt = PROMPT.format(
        today=f"{date.today():%Y年%m月%d日}", angles=ws.covered_angles(exclude_ref=ref),
        focus=ws.focus_note(), recent=recent_titles(exclude=name), title_rules=title_rules(),
        rules=rules, assumption=shots.ASSUMPTION,
        shot_guide=shots.shot_guide(), meta=args.meta, transcript=transcript[:8000],
        example=example_json())
    res = llm.ask_json(prompt, max_tokens=8000)
    if not res.get("ok"):
        fail(res.get("reason", "理由不明"), name)
    try:
        src, spec, warns = ask_until_ok(res, name, _fixer(rules))
    except ValueError as e:
        fail(f"もしも型の台本にできませんでした: {e}", name)
    warns = warns + fact_warnings(spec, transcript)
    # 書き出しの直前に連番を取り直す（生成中に別の回が登録されても上書きしない）
    if not args.name:
        fresh = ws.next_name()
        if fresh != name:
            spec["name"] = name = fresh
    print(json.dumps(save(name, src, spec, warns), ensure_ascii=False))


if __name__ == "__main__":
    main()
