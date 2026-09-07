#!/usr/bin/env python3
"""冒頭ボヤき（AI生成の柴犬クリップ）を完成動画の先頭に連結する。

    ./.venv/bin/python prepend_hook.py 柴犬_031.mp4 ../output/本番_031_TikTok.mp4

やること:
  ① 柴犬クリップを本編と同じ規格（1080x1920 / 30fps / yuv420p / AAC 44.1kHz stereo）に揃える
  ② 音声が無いクリップには無音を足す（concat には両方に音声トラックが必要）
  ③ 先頭に連結して <元の名前>_hook.mp4 として書き出す

なぜ再エンコードするか: AI動画生成ツールの出力はfps・解像度・音声形式がまちまちで、
そのまま concat すると音ズレ・カクつきが起きるため。本編側は無劣化コピーできないので
一緒に通す（画質は crf 18 で実用上ほぼ劣化しない）。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

W, H, FPS, AR = 1080, 1920, 30, 44100


def probe(path: Path, stream: str, entries: str) -> str:
    """ffprobe で1項目だけ取る。取れなければ空文字。"""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", stream,
         "-show_entries", entries, "-of", "csv=p=0", str(path)],
        capture_output=True, text=True)
    return r.stdout.strip()


def normalize(src: Path, dst: Path) -> None:
    """本編と同じ規格へ変換。音声が無ければ無音を生成して付ける。"""
    has_audio = bool(probe(src, "a:0", "stream=codec_type"))
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src)]
    if not has_audio:
        print(f"  ※ {src.name} に音声トラックが無いので無音を追加します")
        cmd += ["-f", "lavfi", "-t", "600", "-i",
                f"anullsrc=channel_layout=stereo:sample_rate={AR}"]
    cmd += [
        "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,"
               f"crop={W}:{H},setsar=1,fps={FPS}",
        "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", str(AR), "-ac", "2",
        "-shortest", str(dst),
    ]
    subprocess.run(cmd, check=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="冒頭ボヤきクリップを本編の先頭に連結する")
    ap.add_argument("hook", help="冒頭に足すクリップ（柴犬のボヤき）")
    ap.add_argument("main", help="本編の完成動画（make_video.py の出力）")
    ap.add_argument("-o", "--out", help="出力先（既定: <本編名>_hook.mp4）")
    args = ap.parse_args()

    hook, main = Path(args.hook), Path(args.main)
    for p in (hook, main):
        if not p.exists():
            print(f"[エラー] ファイルが見つかりません: {p}", file=sys.stderr)
            return 1
    out = Path(args.out) if args.out else main.with_name(main.stem + "_hook.mp4")

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        a, b = td / "a.mp4", td / "b.mp4"
        print(f"① 規格を揃える: {hook.name}")
        normalize(hook, a)
        print(f"② 規格を揃える: {main.name}")
        normalize(main, b)

        lst = td / "list.txt"
        lst.write_text(f"file '{a}'\nfile '{b}'\n", encoding="utf-8")
        print("③ 連結")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                        "-i", str(lst), "-c", "copy", str(out)], check=True)

    dur = probe(out, "v:0", "format=duration") or probe(out, "", "format=duration")
    print(f"\n✓ 出力: {out}")
    print(f"  尺: {float(dur):.1f}秒" if dur else "  尺: 取得できず")
    print("  ※ 目標は65〜72秒。超えていたら本編側の台本を削ってください。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
