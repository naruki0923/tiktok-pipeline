#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
縦書きテロップ画像(PNG・透過)を生成する。
完成形の型: 白い角丸ボックス＋黒の極太ゴシック（強調ワードは赤）／縦書き・列は右→左。

単体テスト:
  ./.venv/bin/python telop_render.py "知らないとヤバい 退職後に国保に加入すると高額請求で驚きます" \
      --red "知らないとヤバい" -o /tmp/telop_test.png

パイプラインからは render_telop(...) を呼ぶ。
"""
import argparse

from PIL import Image, ImageDraw, ImageFont

# 既定の見た目
CANVAS_W, CANVAS_H = 1080, 1920
FONT_PATH = "/System/Library/Fonts/ヒラギノ角ゴシック W8.ttc"   # 極太ゴシック
FONT_SIZE = 96
LINE_GAP = 1.02          # 列内の字送り（文字高に対する倍率）
COL_GAP = 1.18           # 列間隔（文字幅に対する倍率）
MAX_ROWS = 15            # 1列あたりの最大文字数（これを超えたら列を増やす）
BOX_PAD = 46             # ボックス内側の余白
BOX_RADIUS = 28
TEXT_BLACK = (26, 26, 26, 255)
TEXT_RED = (214, 32, 32, 255)
BOX_WHITE = (255, 255, 255, 255)

# 縦書きで回転させたい文字（長音・括弧・波ダッシュなど）
ROTATE_CHARS = set("ー〜～（）()「」『』【】〔〕｛｝—－-…‥、。")

# TikTok対策：お金系ワードを伏せ字にする。
#   「円」→ 削除（例: 500万円 → 500万）。◯ を置くより自然に読めるため（2026-07-30 社長指示）。
#   「金」→ ◯ に置換。
# 置換・削除は _wrap_columns と赤字判定の前に text/red 双方へ同じようにかけるので、
# 文字数が変わっても折り返し・先頭一致はズレない。
MASK_CHARS = {"円": "", "金": "◯"}


def _mask_money(s):
    """テロップ表示用に「円」を削除し「金」を ◯ に置換する。"""
    if not s:
        return s
    return "".join(MASK_CHARS.get(ch, ch) for ch in s)


def _load_font(size):
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except OSError:
        # フォールバック
        return ImageFont.truetype("/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc", size)


def _char_img(ch, font, color):
    """1文字を描いた小さな画像を返す（回転が必要な文字は回転）"""
    box = font.getbbox(ch)
    w = max(box[2] - box[0], 1)
    h = max(box[3] - box[1], 1)
    pad = 8
    im = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.text((pad - box[0], pad - box[1]), ch, font=font, fill=color)
    if ch in ROTATE_CHARS:
        im = im.rotate(-90, expand=True)
    return im


# 形態素解析（あれば使う）。助詞・助動詞の直後＝文節の切れ目として改行を許可する。
try:
    import fugashi
    _TAGGER = fugashi.Tagger()
except Exception:
    _TAGGER = None

# フォールバック用（fugashiが無い場合）の簡易助詞判定
JOSHI = set("はがをにへとでもやのかねよ")
JOSHI_MULTI = ["から", "まで", "より", "など", "って", "では", "には", "でも", "しか", "だけ", "ので", "のに", "けど", "とか", "ながら"]


def _break_after_positions(s):
    """改行してよい位置（その位置で s[:p] / s[p:] に割れる p の集合）を返す。
    形態素解析で助詞・助動詞の直後を採用。無ければ簡易判定にフォールバック。"""
    if _TAGGER is not None:
        toks = list(_TAGGER(s))
        pos, idx = set(), 0
        for i, w in enumerate(toks):
            idx += len(w.surface)
            is_joshi = w.feature.pos1 in ("助詞", "助動詞")
            # 助詞が連続する場合（「に|は」→「には」など）は最後の助詞の後だけで切る。
            # そうしないと「あなたのおすすめに/は表示…」のように助詞の途中で割れてしまう。
            next_is_joshi = (i + 1 < len(toks)
                             and toks[i + 1].feature.pos1 in ("助詞", "助動詞"))
            if is_joshi and not next_is_joshi:
                pos.add(idx)
        return pos
    # フォールバック（簡易）
    pos = set()
    for i, ch in enumerate(s):
        if ch in JOSHI:
            pos.add(i + 1)
    for w in JOSHI_MULTI:
        start = 0
        while (idx := s.find(w, start)) >= 0:
            pos.add(idx + len(w))
            start = idx + 1
    return pos


def _is_joined(a, b):
    """a と b の間で改行すると単語を割ってしまう（数字同士・英字同士）か。"""
    return (a.isdigit() and b.isdigit()) or (a.isascii() and a.isalpha() and b.isascii() and b.isalpha())


def _safe_pos(s, pos):
    """数字・英字の途中に来た改行位置を、手前の安全な境界までずらす。"""
    pos = max(1, min(pos, len(s) - 1))
    while 1 < pos < len(s) and _is_joined(s[pos - 1], s[pos]):
        pos -= 1
    return pos


def _split_para(s, max_rows):
    """1フレーズを列に分割。基本1列、収まらなければ助詞の後で改行する。"""
    import math
    n = len(s)
    if n <= max_rows:
        return [s]
    breaks = _break_after_positions(s)
    n_cols = math.ceil(n / max_rows)

    if n_cols == 2:
        # 2列: 助詞の切れ目で、なるべく中央に近い位置を選ぶ
        # 助詞境界は多少 max_rows を超えても優先（単語の途中で切るより良い。高さはフォント自動縮小で吸収）
        mid = n / 2
        lo, hi = max(1, n - max_rows - 4), min(max_rows + 4, n - 1)
        cands = [p for p in breaks if lo <= p <= hi]
        pos = min(cands, key=lambda p: abs(p - mid)) if cands else _safe_pos(s, round(mid))
        return [s[:pos], s[pos:]]

    # 3列以上: 左から詰めつつ、各列の終わりを助詞の切れ目に合わせる
    cols, start = [], 0
    while start < n:
        end = min(start + max_rows, n)
        if end < n:
            cands = [p for p in breaks if start + 3 <= p <= end]
            end = max(cands) if cands else _safe_pos(s, end)
        cols.append(s[start:end])
        start = end
    return cols


def _wrap_columns(text, max_rows):
    """明示的な改行を尊重しつつ、助詞の切れ目で列に折り返す。列のリスト(文字列)を返す。"""
    columns = []
    for para in text.split("\n"):
        para = para.strip()
        if not para:
            continue
        columns.extend(_split_para(para, max_rows))
    return columns


def render_telop(text, out_path, red=None, font_size=FONT_SIZE, max_rows=MAX_ROWS,
                 canvas=(CANVAS_W, CANVAS_H), center_y_ratio=0.5):
    """
    縦書きテロップPNGを生成。
      text: 表示文字列（\n で列を明示区切り可）
      red:  赤で表示する部分文字列（先頭一致のかたまり）。無ければ全部黒。
    """
    cw, ch = canvas
    # お金系ワード（円・金）を ◯ に伏せ字化。red も同じ変換をかけて先頭一致を保つ。
    text = _mask_money(text)
    if red:
        red = _mask_money(red)
    columns = _wrap_columns(text, max_rows)
    if not columns:
        columns = [""]

    # 縦に長い列がキャンバスに収まらない場合はフォントを自動縮小
    max_col_rows = max(len(c) for c in columns) or 1
    avail_h = ch * 0.86 - BOX_PAD * 2
    if int((font_size * 1.1 * LINE_GAP)) * max_col_rows > avail_h:
        font_size = int(avail_h / (max_col_rows * 1.1 * LINE_GAP))
    font = _load_font(font_size)

    # 赤にする文字数（先頭からの連続。text 内の red の位置を単純に先頭一致で判定）
    red_len = 0
    if red:
        plain = text.replace("\n", "")
        if plain.startswith(red):
            red_len = len(red)

    # 文字の基準サイズ
    asc, desc = font.getmetrics()
    glyph_h = asc + desc
    row_step = int(glyph_h * LINE_GAP)
    col_step = int(font_size * COL_GAP)

    # 全体テキストブロックのサイズ
    n_cols = len(columns)
    max_col_rows = max(len(c) for c in columns)
    block_w = col_step * n_cols
    block_h = row_step * max_col_rows

    img = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # ボックス（テキストブロック＋余白）を中央に
    box_w = block_w + BOX_PAD * 2
    box_h = block_h + BOX_PAD * 2
    box_x0 = (cw - box_w) // 2
    box_y0 = int(ch * center_y_ratio - box_h / 2)
    draw.rounded_rectangle(
        [box_x0, box_y0, box_x0 + box_w, box_y0 + box_h],
        radius=BOX_RADIUS, fill=BOX_WHITE,
    )

    # 文字を配置（列は右→左）
    text_x_right = box_x0 + BOX_PAD + block_w - col_step
    text_y_top = box_y0 + BOX_PAD
    char_index = 0
    for col_i, col in enumerate(columns):
        col_x = text_x_right - col_i * col_step
        for row_i, chr_ in enumerate(col):
            color = TEXT_RED if char_index < red_len else TEXT_BLACK
            cim = _char_img(chr_, font, color)
            # 列の中央に寄せる
            cx = col_x + (col_step - cim.width) // 2
            cy = text_y_top + row_i * row_step + (row_step - cim.height) // 2
            img.alpha_composite(cim, (cx, cy))
            char_index += 1

    img.save(out_path)
    return out_path, (box_x0, box_y0, box_w, box_h)


def main():
    ap = argparse.ArgumentParser(description="縦書きテロップPNGを生成")
    ap.add_argument("text", help="表示文字列（\\n で列区切り）")
    ap.add_argument("-o", "--out", default="telop.png")
    ap.add_argument("--red", default=None, help="赤で表示する先頭部分")
    ap.add_argument("--font-size", type=int, default=FONT_SIZE)
    ap.add_argument("--max-rows", type=int, default=MAX_ROWS)
    args = ap.parse_args()
    path, box = render_telop(args.text, args.out, red=args.red,
                             font_size=args.font_size, max_rows=args.max_rows)
    print(f"[出力] {path}  box={box}")


if __name__ == "__main__":
    main()
