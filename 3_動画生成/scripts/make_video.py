#!/usr/bin/env python3
"""台本テキストを渡すだけで完成動画を書き出すラッパー。

    ./.venv/bin/python make_video.py ../音声/台本_013.txt

内部で voicevox_tts.py（音声＋タイミング）→ compose_video.py（合成）を順に実行する。
背景動画・BGM・出力名は指定しなければ自動で決まる。
compose_video.py のオプション（--red-lines / --scene-secs / --tiktok-only 等）は
そのまま書けば透過的に渡される。ただし話速だけは衝突するため
  --speed      → 背景の再生速度（compose_video.py）
  --tts-speed  → ナレーションの話速（voicevox_tts.py）
"""
import argparse
import json
import random
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_BG_DIR = Path("/Volumes/Extreme SSD/素材/動画・画像素材/4K 散歩動画")
VIDEO_EXTS = {".mp4", ".mov", ".m4v"}
PROBE_CACHE = HERE / ".bg_probe_cache.json"


def probe_durations(clips):
    """各クリップの尺(秒)を返す。ffprobe は遅い（4GB級・外付けSSD）ので
    パス+サイズ+更新時刻をキーにキャッシュする。"""
    try:
        cache = json.loads(PROBE_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}

    durs, dirty = {}, False
    for p in clips:
        st = p.stat()
        key = f"{p}|{st.st_size}|{int(st.st_mtime)}"
        if key not in cache:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(p)],
                capture_output=True, text=True)
            try:
                cache[key] = float(out.stdout.strip())
            except ValueError:
                cache[key] = 0.0          # 壊れている/読めない素材は 0 秒扱い→除外される
            dirty = True
        durs[p] = cache[key]

    if dirty:
        try:
            PROBE_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass                          # キャッシュが書けなくても動作には影響しない
    return durs


def pick_backgrounds(bg_dir: Path, seed, min_secs: float):
    """背景動画フォルダから素材を集め、使えないものを除いてシャッフルして返す。

    compose_video.py は場面 i に bgs[i % len(bgs)] を使うので、全部渡せば
    場面数に関係なく順番に使い回される（不足も余りも起きない）。

    - 短すぎる素材を除外: 場面の尺に足りないと背景が音声より短くなり画がズレる
    - 尺が同一の素材を除外: 同じ映像の別名コピー（実例あり）。同じ街が2回出るのを防ぐ
    """
    if not bg_dir.is_dir():
        sys.exit(f"[エラー] 背景動画フォルダが見つかりません: {bg_dir}\n"
                 f"  外付けSSD「Extreme SSD」が接続されているか確認してください。"
                 f"（--bg-dir / --bg で別の場所も指定できます）")
    clips = sorted(p for p in bg_dir.iterdir()
                   if p.suffix.lower() in VIDEO_EXTS and not p.name.startswith("."))
    if not clips:
        sys.exit(f"[エラー] 背景動画が1本もありません: {bg_dir}")

    durs = probe_durations(clips)
    picked, seen_dur = [], {}
    for p in clips:
        d = durs[p]
        if d < min_secs:
            print(f"  [除外] {p.name} … {d:.0f}秒で短すぎる（{min_secs:.0f}秒未満）")
            continue
        dur_key = round(d, 1)             # 別々の散歩動画が0.1秒まで一致することはまず無い
        if dur_key in seen_dur:
            print(f"  [除外] {p.name} … {seen_dur[dur_key]} と同じ映像（尺が完全一致）")
            continue
        seen_dur[dur_key] = p.name
        picked.append(p)

    if not picked:
        sys.exit(f"[エラー] 使える背景動画がありません: {bg_dir}")
    random.Random(seed).shuffle(picked)
    return picked


def find_bgm(bgm_dir: Path):
    cands = sorted(p for p in bgm_dir.glob("*.mp3") if not p.name.startswith("."))
    return cands[0] if cands else None


def needs_tts(script: Path, wav: Path, timing: Path) -> bool:
    """台本より新しい音声＋タイミングが揃っていれば作り直さない。"""
    if not (wav.exists() and timing.exists()):
        return True
    return script.stat().st_mtime > min(wav.stat().st_mtime, timing.stat().st_mtime)


def run_step(cmd, what):
    """子スクリプトを実行。失敗したらトレースバックではなく短い理由で止める。"""
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        sys.exit(f"\n[中断] {what}に失敗しました（終了コード {e.returncode}）。"
                 f"上のエラーを確認してください。")
    except FileNotFoundError:
        sys.exit(f"\n[中断] {what}のコマンドが見つかりません: {cmd[1]}")


def main():
    sys.stdout.reconfigure(line_buffering=True)   # 子プロセスの出力と順番が入れ替わらないように
    ap = argparse.ArgumentParser(
        description="台本 → 音声 → 合成 を一発で回す（未知のオプションは compose_video.py に渡す）")
    ap.add_argument("script", help="台本テキスト（1行1フレーズ・句読点なし・文末は空行）")
    ap.add_argument("--name", default=None, help="出力ベース名（既定: 台本のファイル名）")
    ap.add_argument("--out-dir", default=str(HERE / ".." / "output"), help="出力フォルダ")
    ap.add_argument("--audio-dir", default=str(HERE / ".." / "音声"), help="音声の置き場")
    ap.add_argument("--bg", action="append", default=None,
                    help="背景動画を明示指定（複数可）。未指定なら --bg-dir から自動で選ぶ")
    ap.add_argument("--bg-dir", default=str(DEFAULT_BG_DIR), help="背景動画フォルダ")
    ap.add_argument("--bgm", default=None,
                    help="BGM（YouTube版用）。未指定なら assets/BGM から自動で選ぶ")
    ap.add_argument("--seed", type=int, default=None,
                    help="背景の並び順を固定する乱数シード（同じ値なら毎回同じ背景）")
    ap.add_argument("--min-bg-secs", type=float, default=60.0,
                    help="この秒数より短い背景動画は使わない（既定60）")
    ap.add_argument("--force-tts", action="store_true",
                    help="音声が最新でも作り直す")
    # voicevox_tts.py 側の設定（--speed は compose と衝突するので --tts-speed）
    ap.add_argument("--tts-speed", type=float, default=1.2, help="ナレーションの話速")
    ap.add_argument("--speaker", default="青山龍星", help="話者名")
    ap.add_argument("--style", default="ノーマル", help="スタイル名")
    ap.add_argument("--host", default=None, help="VOICEVOX ENGINE の URL")
    ap.add_argument("--gap", type=float, default=None, help="行間の無音秒数")
    ap.add_argument("--blank-gap", type=float, default=None, help="空行（文末）の無音秒数")
    args, compose_args = ap.parse_known_args()

    script = Path(args.script).resolve()
    if not script.exists():
        sys.exit(f"[エラー] 台本が見つかりません: {script}")

    name = args.name or script.stem
    audio_dir = Path(args.audio_dir).resolve()
    out_dir = Path(args.out_dir).resolve()
    audio_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = audio_dir / name
    wav, timing = Path(f"{stem}.wav"), Path(f"{stem}.timing.json")  # voicevox_tts.py と同じ組み立て方

    print(f"■ 台本: {script}\n■ 出力名: {name}")

    # ① 音声生成（VOICEVOX）
    if args.force_tts or needs_tts(script, wav, timing):
        cmd = [sys.executable, str(HERE / "voicevox_tts.py"), str(script), "-o", str(stem),
               "--speaker", args.speaker, "--style", args.style, "--speed", str(args.tts_speed)]
        if args.host is not None:
            cmd += ["--host", args.host]
        if args.gap is not None:
            cmd += ["--gap", str(args.gap)]
        if args.blank_gap is not None:
            cmd += ["--blank-gap", str(args.blank_gap)]
        print("\n──① 音声生成（VOICEVOX.app を起動しておくこと）")
        run_step(cmd, "音声生成（voicevox_tts.py）")
    else:
        print(f"\n──① 音声生成 … スキップ（{wav.name} が台本より新しい。作り直すなら --force-tts）")

    total = json.loads(timing.read_text(encoding="utf-8")).get("total", 0)

    # ② 背景・BGM を決める
    print("\n──② 背景素材の選定")
    if args.bg:
        bgs = [Path(b) for b in args.bg]
    else:
        bgs = pick_backgrounds(Path(args.bg_dir), args.seed, args.min_bg_secs)
        print("  使う順: " + " → ".join(p.name for p in bgs[:4])
              + (" …" if len(bgs) > 4 else ""))
    bgm = Path(args.bgm) if args.bgm else find_bgm(HERE / ".." / "assets" / "BGM")

    # ③ 合成
    cmd = [sys.executable, str(HERE / "compose_video.py"),
           "--audio", str(wav), "--timing", str(timing),
           "--out-dir", str(out_dir), "--name", name]
    for b in bgs:
        cmd += ["--bg", str(b)]
    if bgm:
        cmd += ["--bgm", str(bgm)]
    cmd += compose_args  # --red-lines / --scene-secs / --tiktok-only などをそのまま渡す

    print(f"\n──③ 合成（ナレーション {total:.1f}秒 / 背景候補 {len(bgs)}本"
          f"{' / BGM ' + bgm.name if bgm else ' / BGMなし'}）")
    t0 = time.time()
    run_step(cmd, "合成（compose_video.py）")

    # 今回書き出したものだけを出す（前回の残りを「完成」と誤表示しないため）
    made = [p for p in sorted(out_dir.glob(f"{name}_*.mp4")) if p.stat().st_mtime >= t0 - 1]
    print("\n■ 完成")
    for p in made:
        print(f"  {p}  ({p.stat().st_size / 1e6:.1f}MB)")


if __name__ == "__main__":
    main()
