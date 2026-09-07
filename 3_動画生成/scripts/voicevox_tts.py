#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VOICEVOX でナレーション音声を自動生成するスクリプト。

前提:
  - ローカルで VOICEVOX ENGINE が起動していること（VOICEVOX.app を開く or engine を起動）
    デフォルト: http://127.0.0.1:50021
  - ffmpeg が入っていること（mp3出力・結合に使用）

入力:
  - テキストファイル（1行1フレーズ。AI音声用整形プロンプトで 30字改行・句読点なし にしたもの）
    空行は「少し長めの間（ま）」として扱う。

出力:
  - 1本に結合した wav（と mp3）

使い方:
  python3 voicevox_tts.py 台本.txt -o ../音声/narration_001
  # 話者や速度を変える場合
  python3 voicevox_tts.py 台本.txt -o out --speaker "青山龍星" --style "ノーマル" --speed 1.2
"""
import argparse
import io
import json
import sys
import time
import wave
import urllib.request
import urllib.parse
import subprocess
import shutil

DEFAULT_HOST = "http://127.0.0.1:50021"


def http_get(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def http_post_query(url, query_json):
    """audio_query の結果(JSON)を渡して wav バイト列を得る"""
    data = json.dumps(query_json).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read()


def find_speaker_id(host, speaker_name, style_name):
    """話者名・スタイル名から speaker(style) id を探す。見つからなければ候補を表示。"""
    speakers = http_get(f"{host}/speakers")
    # 完全一致を優先
    for sp in speakers:
        if sp["name"] == speaker_name:
            styles = sp["styles"]
            for st in styles:
                if st["name"] == style_name:
                    return st["id"], sp["name"], st["name"]
            # スタイル名が合わなければ先頭スタイル
            st = styles[0]
            return st["id"], sp["name"], st["name"]
    # 部分一致
    for sp in speakers:
        if speaker_name in sp["name"]:
            st = sp["styles"][0]
            return st["id"], sp["name"], st["name"]
    # 見つからない → 一覧を出して終了
    print(f"[エラー] 話者 '{speaker_name}' が見つかりませんでした。利用可能な話者:", file=sys.stderr)
    for sp in speakers:
        styles = ", ".join(st["name"] for st in sp["styles"])
        print(f"  - {sp['name']}  (styles: {styles})", file=sys.stderr)
    sys.exit(1)


def audio_query(host, text, speaker_id):
    """テキストから音声合成用クエリ(JSON)を取得する"""
    q = urllib.parse.urlencode({"text": text, "speaker": speaker_id})
    req = urllib.request.Request(f"{host}/audio_query?{q}", data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def synth_line(host, text, speaker_id, speed):
    """1フレーズを合成して wav バイト列を返す"""
    query = audio_query(host, text, speaker_id)
    query["speedScale"] = speed
    # VOICEVOX が各行の前後に入れる無音をなくし、間は --gap で完全に制御する
    query["prePhonemeLength"] = 0.0
    query["postPhonemeLength"] = 0.0
    return http_post_query(f"{host}/synthesis?speaker={speaker_id}", query)


def read_wav(wav_bytes):
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        params = w.getparams()
        frames = w.readframes(w.getnframes())
    return params, frames


def make_silence(params, seconds):
    n = int(params.framerate * seconds)
    return b"\x00" * (n * params.sampwidth * params.nchannels)


def main():
    ap = argparse.ArgumentParser(description="VOICEVOX でナレーション音声を生成")
    ap.add_argument("textfile", help="台本テキスト（1行1フレーズ・句読点なし）")
    ap.add_argument("-o", "--out", default="narration", help="出力ファイル名（拡張子なし）")
    ap.add_argument("--host", default=DEFAULT_HOST, help="VOICEVOX ENGINE の URL")
    ap.add_argument("--speaker", default="青山龍星", help="話者名")
    ap.add_argument("--style", default="ノーマル", help="スタイル名")
    ap.add_argument("--speed", type=float, default=1.2, help="話速（speedScale）")
    ap.add_argument("--gap", type=float, default=0.12, help="行間（読点）の無音秒数")
    ap.add_argument("--blank-gap", type=float, default=0.25, help="空行（句点・文末）の無音秒数")
    ap.add_argument("--no-mp3", action="store_true", help="mp3 変換をしない")
    args = ap.parse_args()

    # 起動確認
    try:
        ver = http_get(f"{args.host}/version")
    except Exception as e:
        print(f"[エラー] VOICEVOX ENGINE に接続できません ({args.host})。VOICEVOX を起動してください。\n  詳細: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"VOICEVOX ENGINE version: {ver}")

    speaker_id, sp_name, st_name = find_speaker_id(args.host, args.speaker, args.style)
    print(f"話者: {sp_name} / {st_name} (id={speaker_id}) / speed={args.speed}")

    with open(args.textfile, encoding="utf-8") as f:
        lines = [ln.rstrip("\n") for ln in f]

    out_params = None
    out_frames = bytearray()
    total = sum(1 for ln in lines if ln.strip())
    done = 0
    timings = []          # 各行の {text, start, end, sent} （テロップ表示用）
    sentence_ends = []    # 文末（空行＝。）の時刻
    cursor = 0.0          # 現在の累積秒数
    sent_idx = 0          # 文番号（空行ごとに +1）。SEを文単位で1回にするため

    def frames_seconds(params, frames):
        return len(frames) / (params.framerate * params.sampwidth * params.nchannels)

    for ln in lines:
        if not ln.strip():
            if out_params is not None:
                # 空行＝文末（。）。次の行から新しい文にする
                sentence_ends.append(round(cursor, 3))
                sent_idx += 1
                out_frames += make_silence(out_params, args.blank_gap)
                cursor += args.blank_gap
            continue
        wav_bytes = synth_line(args.host, ln.strip(), speaker_id, args.speed)
        params, frames = read_wav(wav_bytes)
        if out_params is None:
            out_params = params
        speech_sec = frames_seconds(out_params, frames)
        start = cursor
        out_frames += frames
        cursor += speech_sec
        out_frames += make_silence(out_params, args.gap)
        cursor += args.gap
        # テロップは次の行が始まるまで（＝行間の無音も含めて）表示し続ける
        timings.append({"text": ln.strip(), "start": round(start, 3), "end": round(cursor, 3), "sent": sent_idx})
        done += 1
        print(f"  [{done}/{total}] ({start:5.1f}s) {ln.strip()[:20]}")
        time.sleep(0.02)

    if out_params is None:
        print("[エラー] テキストが空です。", file=sys.stderr)
        sys.exit(1)

    wav_path = f"{args.out}.wav"
    with wave.open(wav_path, "wb") as w:
        w.setparams(out_params)
        w.writeframes(bytes(out_frames))
    print(f"[出力] {wav_path}")

    timing_path = f"{args.out}.timing.json"
    with open(timing_path, "w", encoding="utf-8") as tf:
        json.dump({"total": round(cursor, 3), "lines": timings, "sentence_ends": sentence_ends},
                  tf, ensure_ascii=False, indent=2)
    print(f"[出力] {timing_path}（全体 {cursor:.1f}秒 / {len(timings)}行）")

    if not args.no_mp3:
        if shutil.which("ffmpeg"):
            mp3_path = f"{args.out}.mp3"
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", wav_path, "-codec:a", "libmp3lame", "-q:a", "2", mp3_path],
                check=True,
            )
            print(f"[出力] {mp3_path}")
        else:
            print("[警告] ffmpeg が無いため mp3 変換はスキップしました。")


if __name__ == "__main__":
    main()
