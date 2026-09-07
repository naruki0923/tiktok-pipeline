#!/usr/bin/env python3
"""校正済みの文章を make_video.py 用の台本テキストに整形する（AI音声用整形の自動化）。

    # このフォルダの .venv（fugashi / Pillow 入り）で実行する
    ./.venv/bin/python format_script.py 原稿.txt -o ../../3_動画生成/音声/本番_014.txt

入力: ふつうの文章（句読点あり・段落自由）。2_台本生成 の校正済みテキスト。
出力: 1行1フレーズ（助詞・文節の切れ目で30字以内に改行）／句読点なし／文末は空行。
      → そのまま `3_動画生成/scripts/make_video.py` に渡せる形。

整形と同時に、CTA（LINE誘導）文・いいね文の有無・行の長さ・句読点の残りを検査し、
問題があれば stderr に警告する（--strict なら警告時に終了コード1）。

改行位置の判定（助詞・文節の切れ目）は 3_動画生成 の telop_render.py と同じロジックを
共有する。台本の改行位置とテロップ内の改行位置がズレないようにするため。
"""
import argparse
import re
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMPOSE_SCRIPTS = HERE / ".." / ".." / "3_動画生成" / "scripts"

# 改行位置ロジックを telop_render.py から借りる（1箇所に集約して一貫させる）
sys.path.insert(0, str(COMPOSE_SCRIPTS.resolve()))
try:
    from telop_render import _break_after_positions, _safe_pos  # noqa: E402
except Exception as e:  # fugashi 未導入 or import 失敗
    sys.exit(f"[エラー] telop_render の読み込みに失敗しました: {e}\n"
             f"  3_動画生成/scripts/.venv/bin/python で実行していますか？（fugashi が必要）")

# --- 文末・句読点まわりの文字 -------------------------------------------------
SENT_END = "。．！？!?"          # ここで文を分ける（＝出力では空行になる）
DROP_CHARS = "、，。．「」『』（）()｢｣、･"  # 音声・テロップに不要で消す記号（中黒「・」は残す）
KEEP_PUNCT_RE = re.compile(r"[、，。．！？!?「」『』…‥]")  # 残っていたら警告する句読点

# --- CTA / いいね の判定キーワード（compose_video.py のオーバーレイ判定に合わせる）---
CTA_KEYWORDS = ["個別面談", "面談", "説明会", "プロフィール", "リンク", "公式", "登録", "概要欄"]
LIKE_KEYWORDS = ["いいね", "保存"]
# 1行目に置くと冒頭2秒を捨てる「前置き」語（5_分析 実測フィードバック）
WEAK_HOOK_PREFIXES = ["実は", "今回は", "みなさん", "みんな", "こんにちは", "どうも", "えー", "あの"]

# --- 尺の推定（2026-07-30、完成動画14本の実測で最小二乗較正）---
# 読み上げ 7.85字/秒 ＋ 文末の間 0.54秒×(文数-1)。平均誤差 1.1秒（旧式 8.3/0.25 は 9.9秒ずれていた）。
# 旧式は尺を約10秒短く見積もるため、それを信じて書くと必ず長くなっていた。
CHARS_PER_SEC = 7.85
PAUSE_PER_SENTENCE = 0.54
MAX_SECONDS = 75         # これを超えたら警告
TARGET_SECONDS = (65, 72)


def estimate_seconds(chars: int, n_sentences: int) -> float:
    """整形後の字数と文数から完成動画の尺（秒）を推定する。"""
    return chars / CHARS_PER_SEC + max(0, n_sentences - 1) * PAUSE_PER_SENTENCE


def split_sentences(text: str):
    """文章を文に分割する。SENT_END と改行の両方を区切りに使う。"""
    text = unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\r", "\n")
    # 文末記号の直後に区切りを入れてから、改行と合わせて分割
    marked = re.sub(f"([{re.escape(SENT_END)}])", r"\1\n", text)
    return [s.strip() for s in marked.split("\n") if s.strip()]


def clean_sentence(s: str) -> str:
    """1文から不要な記号・空白を除く（音声・テロップ用）。"""
    s = "".join(ch for ch in s if ch not in DROP_CHARS)
    s = re.sub(r"\s+", "", s)          # 全角/半角スペースは詰める
    return s


def split_phrases(sentence: str, max_len: int):
    """1文を、助詞・文節の切れ目で max_len 字以内のフレーズ（＝テロップ1枚）に割る。

    - 助詞境界（telop_render と同じ判定）を優先
    - 境界が無ければ数字・英字を割らない安全位置で強制分割
    """
    n = len(sentence)
    if n <= max_len:
        return [sentence]
    breaks = _break_after_positions(sentence)
    phrases, start = [], 0
    while start < n:
        if n - start <= max_len:
            phrases.append(sentence[start:])
            break
        window_end = start + max_len
        cands = [p for p in breaks if start < p <= window_end]
        if cands:
            end = max(cands)
        else:
            end = _safe_pos(sentence, window_end)   # 数字/英字の途中を避ける
            if end <= start:
                end = window_end                    # 最終手段（無限ループ防止）
        phrases.append(sentence[start:end])
        start = end
    return phrases


def format_script(text: str, max_len: int):
    """文章 → (台本行のリスト, 文ごとのフレーズ数)。

    voicevox_tts.py は「行区切り＝小さい間、空行＝大きい間＋文末SE」として読む。そこで
      句点「。」→ 空行（文の区切り・大きい間）
      読点「、」→ 行区切り（小さい間・テロップ切替）※元台本のリズムを再現
    にマッピングする。読点で割った断片がまだ長ければ助詞境界でさらに割る。
    """
    lines, per_sentence = [], []
    for sent in split_sentences(text):
        phrases = []
        for chunk in re.split(r"[、，]", sent):      # 読点でまず区切る＝小さい間
            cleaned = clean_sentence(chunk)
            if cleaned:
                phrases.extend(split_phrases(cleaned, max_len))
        if not phrases:
            continue
        per_sentence.append(len(phrases))
        if lines:
            lines.append("")             # 文の区切り＝空行（voicevox_tts が文末=間+SEとして扱う）
        lines.extend(phrases)
    return lines, per_sentence


def lint(lines, max_len: int, n_sentences: int = 0):
    """整形後の台本を検査し、警告メッセージのリストを返す。"""
    warns = []
    phrases = [ln for ln in lines if ln.strip()]
    joined = "".join(phrases)

    # 1) 行が長すぎる（テロップで見切れる／読みにくい）
    for i, ln in enumerate(lines, 1):
        if len(ln) > max_len:
            warns.append(f"{i}行目が{len(ln)}字（{max_len}字以内推奨）: {ln}")

    # 2) 句読点・かぎ括弧の消し残り
    leftover = sorted({m.group() for ln in lines for m in KEEP_PUNCT_RE.finditer(ln)})
    if leftover:
        warns.append(f"句読点/記号が残っています: {' '.join(leftover)}（除去対象の見直しを）")

    # 3) CTA（LINE誘導）文があるか … CLAUDE.md で必須
    if not any(k in joined for k in CTA_KEYWORDS):
        warns.append("CTA（LINE誘導）文が見当たりません。"
                     "末尾に「無料の個別面談に参加してみてください」等を入れてください")

    # 4) いいね・保存の一文があるか … 入れるとオーバーレイが自動で乗る（任意）
    if not all(any(k in p for p in phrases) for k in LIKE_KEYWORDS):
        warns.append("「いいね」「保存」を促す文が見当たりません（任意。"
                     "入れると いいね・保存オーバーレイが自動で入ります）")

    # 5) 冒頭2秒（1行目）が弱いフックでないか … 5_分析の実測フィードバック（2026-07-21）。
    #    全投稿が「0:02」で最大離脱。前置き語で始まる1行目は維持率が明確に低かった。
    #    台本作成ルール.md「★冒頭2秒（1行目）の作り方」を参照。
    if phrases:
        first = phrases[0]
        if any(first.startswith(w) for w in WEAK_HOOK_PREFIXES):
            warns.append(
                f"1行目が前置き（弱いフック）で始まっています: 「{first}」。"
                "冒頭2秒で最大離脱が起きるため、数字/損失/名指しで一撃目から本題に。"
                "→ 台本作成ルール.md『★冒頭2秒（1行目）の作り方』")

    # 6) 尺（字数）… 5_分析の実測（2026-07-30・完成動画14本）で 6.9字/秒。
    #    尺×視聴維持率 r=-0.48、フォロワー転換率×維持率 r=+0.44 ＝ 長いほどフォロワーが減る。
    est = estimate_seconds(len(joined), n_sentences or (sum(1 for ln in lines if not ln.strip()) + 1))
    if est > MAX_SECONDS:
        over = est - TARGET_SECONDS[1]
        warns.append(
            f"長すぎます: 推定{est:.0f}秒（上限{MAX_SECONDS}秒／目標{TARGET_SECONDS[0]}〜{TARGET_SECONDS[1]}秒）。"
            f"約{over:.0f}秒＝{over * CHARS_PER_SEC:.0f}字ぶん削ってください。"
            "文を薄めるのではなく“項目数を減らす”で削ること（6つ→3つ）。"
            "→ 台本作成ルール.md『★尺の目安』")

    # 7) 中盤のフォロー誘導があるか … 末尾CTAはフル視聴率5.7%＝20人に1人にしか届いていない。
    #    「フォロー」が1回だけ＝末尾のみの可能性が高い。
    follow_hits = sum(1 for p in phrases if "フォロー" in p)
    if follow_hits <= 1:
        warns.append(
            "中盤のフォロー誘導が見当たりません（「フォロー」の出現が"
            f"{follow_hits}回）。1つ目の項目の直後に3行入れてください。"
            "→ 台本作成ルール.md『③.5 中盤フォロー誘導』")
    return warns


def main():
    ap = argparse.ArgumentParser(
        description="校正済み文章を make_video.py 用の台本テキストに整形する")
    ap.add_argument("input", help="校正済みの原稿テキスト（句読点あり・段落自由）")
    ap.add_argument("-o", "--out", default=None,
                    help="出力ファイル（省略時は標準出力）")
    ap.add_argument("--max-len", type=int, default=30,
                    help="1フレーズ（テロップ1枚）の最大文字数（既定30）")
    ap.add_argument("--strict", action="store_true",
                    help="警告があれば終了コード1で終わる（自動化で弾く用）")
    args = ap.parse_args()

    src = Path(args.input)
    if not src.exists():
        sys.exit(f"[エラー] 原稿が見つかりません: {src}")

    lines, per_sentence = format_script(src.read_text(encoding="utf-8"), args.max_len)
    if not lines:
        sys.exit("[エラー] 整形できる文がありません（入力が空？）")
    body = "\n".join(lines) + "\n"

    if args.out:
        Path(args.out).write_text(body, encoding="utf-8")
        print(f"[出力] {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(body)

    # --- サマリ・警告は stderr（-o なしでも本文をパイプできるように）---
    phrases = [ln for ln in lines if ln.strip()]
    chars = sum(len(p) for p in phrases)
    est = estimate_seconds(chars, len(per_sentence))
    print(f"\n■ 整形結果: {len(per_sentence)}文 / {len(phrases)}フレーズ / {chars}字 / 推定 約{est:.0f}秒"
          f"（目標{TARGET_SECONDS[0]}〜{TARGET_SECONDS[1]}秒）",
          file=sys.stderr)

    warns = lint(lines, args.max_len, len(per_sentence))
    if warns:
        print("\n⚠ 警告:", file=sys.stderr)
        for w in warns:
            print(f"  - {w}", file=sys.stderr)
        if args.strict:
            sys.exit(1)
    else:
        print("✓ 問題なし（そのまま make_video.py に渡せます）", file=sys.stderr)


if __name__ == "__main__":
    main()
