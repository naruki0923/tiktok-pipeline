#!/usr/bin/env python3
"""AI生成の「冒頭ボヤき」クリップを、本編に繋げる形へ加工する。

    ./.venv/bin/python make_hook_clip.py 生クリップ.mp4 \
        --line "0.1-3.0:退職金は紙1枚で|2割天引きよ" \
        --delogo 574,1130,60,62 --crop-top 205 \
        -o ../output/hook_031.mp4

やること:
  ① ウォーターマーク除去
     - --crop-top: 上部を切る（上に出る透かし・AIが描いた架空の日本語看板の除去）
     - --delogo:   x,y,w,h の矩形を周囲の色で埋める（隅のキラキラ等。無地の背景に有効）
     切ったあとは 9:16 になるよう左右を中央基準で詰め直す。
  ② 本編と同じ規格へ … 1080x1920 / 30fps / yuv420p / AAC 44.1kHz stereo
  ③ テロップ … 犬・猫系トーク動画と同じ「極太丸ゴシック・白文字・分厚い黒フチ」を
     Pillow でPNG化して重ねる。中央揃え・縦中央（--telop-y で変更可）。
     ※ ffmpeg の drawtext ではフチが1重しか引けず、あの太さを再現できないためPNG方式。

--line の書式:  開始秒-終了秒:表示テキスト     （改行は | で区切る）

そのあと prepend_hook.py で本編の先頭に連結する。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H, FPS, AR = 1080, 1920, 30, 44100

# 参考にした猫・犬系トーク動画のテロップ＝極太の丸ゴシック＋分厚い黒フチ。
# macOS標準で日本語が出て丸ゴシックなのはこれだけ（Monaco等の等幅は日本語グリフ無し）。
DEFAULT_FONT = "/System/Library/Fonts/ヒラギノ丸ゴ ProN W4.ttc"
FATTEN = 1       # 白を太らせる量。丸ゴW4を少しだけ太らせる（3以上だと画数の多い漢字が潰れる）
BORDER = 16      # 黒フチの太さ
# 行間。フチは行の上下に BORDER 分はみ出すので、その2倍＋余白を空けないと行同士がくっつく。
SPACING = BORDER * 2 + 14


def parse_line(spec: str) -> tuple[float, float, str]:
    m = re.match(r"^\s*([\d.]+)\s*-\s*([\d.]+)\s*:\s*(.+)$", spec, re.S)
    if not m:
        raise SystemExit(f"[エラー] --line の書式が不正です: {spec}\n"
                         '  正: --line "0.1-3.0:退職金は紙1枚で|2割天引きよ"')
    return float(m.group(1)), float(m.group(2)), m.group(3).replace("|", "\n")


def render_telop_png(text: str, path: Path, font_path: str, size: int, y_ratio: float) -> None:
    """透明背景に、白文字＋分厚い黒フチのテロップを描いて保存する。

    ⚠️ multiline_text は使わないこと。Pillow は stroke_width を行送りの計算に含めるため、
    黒フチ(太い)と白太らせ(細い)で2回描くと **2行目以降だけ位置がズレて二重に見える**。
    行ごとに y を自前で決めて draw.text() すれば、両方の描画が必ず同じ位置に載る。
    """
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    lines = text.split("\n")

    def fit(sz: int) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(font_path, sz)

    font = fit(size)
    # 一番長い行がキャンバスに収まるまで縮める（フチの分も内側に入れる）
    while size > 24:
        widest = max(font.getbbox(ln)[2] - font.getbbox(ln)[0] for ln in lines)
        if widest + BORDER * 2 <= W - 80:
            break
        size -= 4
        font = fit(size)

    asc, desc = font.getmetrics()
    line_h = asc + desc
    pitch = line_h + SPACING                      # 行の送り（フチが重ならない間隔）
    block_h = pitch * len(lines) - SPACING
    top = (H - block_h) * y_ratio

    for i, ln in enumerate(lines):
        lb = font.getbbox(ln)
        lx = (W - (lb[2] - lb[0])) / 2 - lb[0]
        ly = top + i * pitch
        # 黒フチ → 白の太らせ の順に、同じ座標へ重ねる
        d.text((lx, ly), ln, font=font, fill="white",
               stroke_width=BORDER, stroke_fill="black")
        d.text((lx, ly), ln, font=font, fill="white",
               stroke_width=FATTEN, stroke_fill="white")
    img.save(path)


def main() -> int:
    ap = argparse.ArgumentParser(description="冒頭ボヤきクリップを加工する")
    ap.add_argument("src", help="AI生成の生クリップ")
    ap.add_argument("-o", "--out", required=True, help="出力先")
    ap.add_argument("--line", action="append", default=[],
                    help="テロップ。'開始秒-終了秒:本文'（改行は |）。複数可")
    ap.add_argument("--font", default=DEFAULT_FONT)
    ap.add_argument("--font-size", type=int, default=76)
    ap.add_argument("--telop-y", type=float, default=0.5,
                    help="テロップの縦位置 0.0(上)〜1.0(下)。既定0.5=縦中央")
    ap.add_argument("--crop-top", type=int, default=0,
                    help="上から切り落とすpx（透かし・架空看板の除去）")
    ap.add_argument("--delogo", default=None, metavar="x,y,w,h",
                    help="この矩形を周囲の色で埋めて透かしを消す（元動画の座標）")
    args = ap.parse_args()

    src, out = Path(args.src), Path(args.out)
    if not src.exists():
        print(f"[エラー] ファイルが見つかりません: {src}", file=sys.stderr)
        return 1
    if not Path(args.font).exists():
        print(f"[エラー] フォントが見つかりません: {args.font}", file=sys.stderr)
        return 1

    dim = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=width,height", "-of", "csv=p=0", str(src)],
                         capture_output=True, text=True).stdout.strip()
    sw, sh = (int(x) for x in dim.split(",")[:2])

    chain = []
    if args.delogo:
        x, y, w, h = (int(v) for v in args.delogo.split(","))
        chain.append(f"delogo=x={x}:y={y}:w={w}:h={h}")
    # 上を切ってから 9:16 になるよう左右も詰める
    ch_ = sh - args.crop_top
    cw = min(sw, int(ch_ * 9 / 16))
    ch_ = min(ch_, int(cw * 16 / 9))
    cx = (sw - cw) // 2
    chain += [f"crop={cw}:{ch_}:{cx}:{args.crop_top}", f"scale={W}:{H}", "setsar=1", f"fps={FPS}"]

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        pngs = []
        for i, spec in enumerate(args.line):
            st, en, text = parse_line(spec)
            p = td / f"t{i}.png"
            render_telop_png(text, p, args.font, args.font_size, args.telop_y)
            pngs.append((p, st, en))

        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src)]
        for p, _, _ in pngs:
            cmd += ["-i", str(p)]
        fc = f"[0:v]{','.join(chain)}[v0];"
        cur = "v0"
        for i, (_, st, en) in enumerate(pngs):
            nxt = f"v{i+1}"
            fc += (f"[{cur}][{i+1}:v]overlay=0:0:enable='between(t,{st},{en})'[{nxt}];")
            cur = nxt
        fc = fc.rstrip(";")
        out.parent.mkdir(parents=True, exist_ok=True)
        cmd += ["-filter_complex", fc, "-map", f"[{cur}]", "-map", "0:a?",
                "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-ar", str(AR), "-ac", "2", str(out)]
        subprocess.run(cmd, check=True)

    dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout.strip()
    print(f"✓ 出力: {out}")
    print(f"  元 {sw}x{sh} → crop {cw}x{ch_} (上{args.crop_top}px除去) → {W}x{H}")
    if dur:
        print(f"  尺: {float(dur):.1f}秒")
    print(f"\n次: ./.venv/bin/python prepend_hook.py {out} ../output/本番_XXX_TikTok.mp4")
    return 0


if __name__ == "__main__":
    sys.exit(main())
