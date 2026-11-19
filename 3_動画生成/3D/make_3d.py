#!/usr/bin/env python3
"""もしも型3D動画: spec → ナレーション → 3D書き出し(world.py) → 字幕・カウンター等を重ねて TikTok/YouTube の2本。

  cd 3_動画生成/3D
  ../scripts/.venv/bin/python make_3d.py ../音声/本番_150.3d.json                 # 本番（約1時間）
  ../scripts/.venv/bin/python make_3d.py ../音声/本番_150.3d.json --preview       # 各ショットの静止画だけ（数分）
  ../scripts/.venv/bin/python make_3d.py ../音声/本番_150.3d.json --skip-render   # 3Dは前回のまま重ねる所だけ
  ../scripts/.venv/bin/python make_3d.py --check                                 # 必要な物が揃っているか

出力は 3_動画生成/output/<name>_TikTok.mp4 / _YouTube.mp4（旧形式と同じ置き場・同じ名前）。
spec は 2_台本生成/scripts/write_moshimo.py が作る（形は shots.py の build_spec）。
SE は入れない（2026-11-17 社長指示）。LINE誘導テンプレの音だけ残す。

前提: VOICEVOX 起動中・/Applications/Blender.app・../assets/3D/mixamo/
"""
import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "scripts"))
import voicevox_tts as vv  # noqa: E402
from make_video import find_bgm  # noqa: E402
from telop_render import _mask_money  # noqa: E402

BLENDER = os.environ.get("BLENDER", "/Applications/Blender.app/Contents/MacOS/Blender")
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
FFPROBE = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
FPS, W, H = 30, 1080, 1920
W8 = "/System/Library/Fonts/ヒラギノ角ゴシック W8.ttc"
W6 = "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc"
HOST = vv.DEFAULT_HOST
GAP, BLANK_GAP, LEAD, TAIL = 0.12, 0.25, 0.3, 0.6      # voicevox_tts.py と同じ間
LINE_OV = ROOT / "テンプレート/LINE誘導.mp4"
CHROMA = "0x73D44C"                                      # compose_video.py と同じ
BGM_DIR = ROOT / "assets/BGM"
MIXAMO = ROOT / "assets/3D/mixamo"
MIXAMO_FILES = ["hero", "staff", "customer", "walk", "idle", "sad", "head", "talk", "sit", "look", "think"]
OUT_DIR = ROOT / "output"
WORK = HERE / "work"
MIN_SECONDS = 60.5          # 社長指示: 必ず1分超え
MIN_FREE_GB = 8             # 連番(JPEG)＋中間mp4の置き場
WALK_SPEED = 1.4            # 歩きの上限(m/秒)。短い区間で滑るように速く歩かないよう、歩く距離を縮める


def sweep_work(keep_days: float = 2.0) -> None:
    """古い作業フォルダ（連番JPEG・中間mp4）を消す。失敗した回は調べられるよう連番を残すので、
    放っておくと無人運用で数GBずつ溜まる。"""
    if not WORK.exists():
        return
    limit = time.time() - keep_days * 86400
    for d in WORK.iterdir():
        if d.is_dir() and d.stat().st_mtime < limit:
            shutil.rmtree(d, ignore_errors=True)


def check_env() -> list[str]:
    """書き出しに要る物が揃っているか。足りない物を返す（空なら準備OK）。"""
    probs = []
    if not Path(BLENDER).exists():
        probs.append(f"Blender が見つかりません: {BLENDER}（brew install --cask blender）")
    missing = [n for n in MIXAMO_FILES if not (MIXAMO / f"{n}.fbx").exists()]
    if missing:
        probs.append(f"Mixamo素材が足りません: {MIXAMO}/ に {', '.join(m + '.fbx' for m in missing)}")
    if not LINE_OV.exists():
        probs.append(f"LINE誘導テンプレがありません: {LINE_OV}")
    for f in (W8, W6):
        if not Path(f).exists():
            probs.append(f"フォントがありません: {f}")
    free = shutil.disk_usage(str(ROOT)).free / 1e9
    if free < MIN_FREE_GB:
        probs.append(f"Macの空きが {free:.0f}GB しかありません（3Dの書き出しに{MIN_FREE_GB}GB以上要る）")
    return probs


# ---------------------------------------------------------------- 1. ナレーション
def narrate(spec, work):
    sid, *_ = vv.find_speaker_id(HOST, "青山龍星", "ノーマル")
    params, out, cur = None, bytearray(), 0.0
    segs = []

    def sil(sec):
        nonlocal cur
        out.extend(vv.make_silence(params, sec))
        cur += sec

    n = len(spec["segments"])
    for i, sg in enumerate(spec["segments"]):
        start, line_starts = None, []
        for line in sg["say"]:
            p, frm = vv.read_wav(vv.synth_line(HOST, line, sid, 1.2))
            if params is None:
                params = p
                sil(LEAD)
            if start is None:
                start = cur
            line_starts.append(cur)
            out.extend(frm)
            cur += len(frm) / (p.framerate * p.sampwidth * p.nchannels)
            sil(GAP)
        if i == n - 1:
            sil(TAIL)
        else:
            sil((0.0 if sg.get("join") else BLANK_GAP) + sg.get("pad", 0.0))
        segs.append({**sg, "start": round(0.0 if i == 0 else start, 3), "end": round(cur, 3),
                     "voice_start": round(start, 3), "line_starts": [round(x, 3) for x in line_starts]})
    # 区間の境目は「次の区間の声の頭」。end を次の start に揃える
    for a, b in zip(segs, segs[1:]):
        a["end"] = b["start"]
    wav = work / "narration.wav"
    with wave.open(str(wav), "wb") as w:
        w.setparams(params)
        w.writeframes(bytes(out))
    return wav, segs, cur


def ffprobe_dur(p):
    r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                       capture_output=True, text=True)
    return float(r.stdout.strip())


def pace_walks(segs):
    """歩くショットの距離を区間の長さに合わせて縮める（終点はそのまま・始点を寄せる）。

    ショットの型は国保もしもの区間の長さで決めた距離なので、短い区間に置くと
    人が滑るように速歩きになる。カメラが人を追うショット（tgt2あり）はカメラの始点も寄せる。
    """
    for s in segs:
        h = s.get("hero") or {}
        if "to" not in h:
            continue
        a, b = h["at"], h["to"]
        dist = math.dist(a, b)
        dur = max(0.1, s["end"] - s["start"])
        f = min(1.0, WALK_SPEED * dur / max(dist, 1e-6))
        if f >= 0.999:
            continue
        h["at"] = [b[k] + (a[k] - b[k]) * f for k in range(3)]
        if "tgt2" in s and "cam2" in s:
            s["cam"] = [s["cam2"][k] + (s["cam"][k] - s["cam2"][k]) * f for k in range(3)]
            s["tgt"] = [s["tgt2"][k] + (s["tgt"][k] - s["tgt2"][k]) * f for k in range(3)]


# ---------------------------------------------------------------- 2. 3D
def render_3d(segs, spec, total, frames_dir, preview):
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True)
    keys = ("start", "end", "scene", "cam", "tgt", "cam2", "tgt2", "lens", "hero", "show", "render")
    shots = [{k: s[k] for k in keys if k in s} for s in segs]
    sj = frames_dir.parent / "shots.json"
    sj.write_text(json.dumps({"fps": FPS, "total": total, "props": spec.get("props") or {},
                              "segments": shots}, ensure_ascii=False))
    cmd = [BLENDER, "-b", "--factory-startup", "-P", str(HERE / "world.py"), "--", str(sj), str(frames_dir)]
    if preview:
        cmd.append("preview")
    logf = frames_dir.parent / "blender.log"
    t0 = time.time()
    with open(logf, "w", encoding="utf-8") as lf:
        r = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, text=True)
    n = len(list(frames_dir.glob("*.jpg")))
    print(f"  3D書き出し {n}枚 / {(time.time() - t0) / 60:.1f}分", flush=True)
    if r.returncode != 0 or not n:
        tail = logf.read_text(encoding="utf-8", errors="replace")[-3000:]
        print(tail)
        sys.exit("Blender の書き出しに失敗")


# ---------------------------------------------------------------- 3. 重ねる物
_fonts = {}


def font(path, size):
    if (path, size) not in _fonts:
        _fonts[(path, size)] = ImageFont.truetype(path, size)
    return _fonts[(path, size)]


def stext(d, xy, s, f, fill=(255, 255, 255), stroke=(20, 20, 20), sw=8, anchor="la"):
    d.text(xy, s, font=f, fill=fill, stroke_width=sw, stroke_fill=stroke, anchor=anchor)


def fit(s, path, size, maxw):
    while size > 30 and font(path, size).getlength(s) > maxw:
        size -= 2
    return font(path, size)


def ease(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def grade(img, kind, t_in):
    """もしも＝暖かく明るく／現実＝彩度を落として青く暗め"""
    if kind == "ideal":
        img = ImageEnhance.Color(img).enhance(1.15)
        r, g, b = img.split()
        r = r.point(lambda v: min(255, int(v * 1.05 + 6)))
        b = b.point(lambda v: int(v * 0.93))
        return Image.merge("RGB", (r, g, b))
    if kind == "real":
        img = ImageEnhance.Color(img).enhance(0.55)
        img = ImageEnhance.Brightness(img).enhance(0.92)
        r, g, b = img.split()
        r = r.point(lambda v: int(v * 0.94))
        b = b.point(lambda v: min(255, int(v * 1.04 + 4)))
        return Image.merge("RGB", (r, g, b))
    return img


BADGE_COL = {"もしもの世界": ((255, 214, 64), (30, 30, 30)), "現実": ((150, 30, 30), (255, 255, 255)),
             "もしも": ((150, 30, 30), (255, 255, 255)),    # 悪いもしも（現実と同じ暗い赤）
             "今の制度": ((40, 150, 90), (255, 255, 255)), "巻き戻し": ((40, 40, 40), (255, 255, 255))}


def overlay(t, segs, spec, i):
    seg = segs[i]
    prev = segs[i - 1] if i else seg
    lt = t - seg["start"]
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    # タイトル帯
    im.alpha_composite(Image.new("RGBA", (W, 250), (255, 255, 255, 220)), (0, 95))
    for k, line in enumerate(spec["title"].split("\n")):
        d.text((W // 2, 160 + k * 92), line, font=fit(line, W8, 74, 980), fill=(25, 25, 25), anchor="mm")
    d.rectangle((0, 338, W, 345), fill=(230, 90, 30, 255))

    # バッジ（右上）
    if seg.get("badge"):
        bg, fg = BADGE_COL[seg["badge"]]
        f = font(W8, 40)
        tw = f.getlength(seg["badge"])
        x1 = W - 40
        d.rounded_rectangle((x1 - tw - 44, 372, x1, 436), 14, fill=(*bg, 235))
        d.text((x1 - tw / 2 - 22, 404), seg["badge"], font=f, fill=fg, anchor="mm")

    # カウンター（左上）
    if seg.get("day") is not None:
        x0, y0 = 56, 395
        if seg.get("rewind"):
            # 巻き戻し：直前のもしも（365日目・117）から 1日目・120 へ戻す
            k = ease(lt / max(0.3, (seg["end"] - seg["start"]) * 0.8))
            src = next(s for s in reversed(segs[:i]) if s.get("day") is not None)
            day = round(src["day"] + (seg["day"] - src["day"]) * k)
            money = round(src["money"] + (seg["money"] - src["money"]) * k)
            col = (255, 255, 255)
        else:
            day = seg["day"]
            m0 = prev.get("money", seg["money"]) if prev.get("day") is not None else seg["money"]
            m1 = seg["money"]
            k = ease(lt / 0.9) if m0 != m1 else 1.0
            money = round(m0 + (m1 - m0) * k)
            real = seg.get("grade") == "real"   # 赤字・減った額の表示は「現実」の時だけ
            col = (255, 70, 60) if real and m1 < spec.get("start_money", 120) else (255, 255, 255)
        stext(d, (x0, y0), _mask_money(spec.get("day_label") or "退職してから"), font(W6, 36), sw=6)
        stext(d, (x0, y0 + 46), f"{day}", font(W8, 120), sw=9)
        nw = font(W8, 120).getlength(f"{day}")
        stext(d, (x0 + nw + 10, y0 + 104), "日目", font(W8, 52), sw=7)
        stext(d, (x0, y0 + 205), _mask_money("手元のお金"), font(W6, 36), sw=6)
        stext(d, (x0, y0 + 250), f"{money}", font(W8, 104), fill=col, sw=9)
        mw = font(W8, 104).getlength(f"{money}")
        stext(d, (x0 + mw + 8, y0 + 298), _mask_money("万円"), font(W8, 52), fill=col, sw=7)
        if not seg.get("rewind") and real and m1 < m0 and lt < 2.2:
            stext(d, (x0 + mw + 70, y0 + 255), f"-{m0 - m1}", font(W8, 48), fill=(255, 70, 60), sw=6)

    # 大字幕＋小字幕
    if seg.get("big"):
        big = _mask_money(seg["big"])
        t0 = seg["voice_start"] - seg["start"]
        sc = 0.82 + 0.18 * ease((lt - t0) / 0.18)
        bf = fit(big, W8, 92, 960)
        layer = Image.new("RGBA", (W, 200), (0, 0, 0, 0))
        stext(ImageDraw.Draw(layer), (W // 2, 100), big, bf, sw=12, anchor="mm")
        if sc < 0.999:
            layer = layer.resize((int(W * sc), int(200 * sc)), Image.LANCZOS)
        im.alpha_composite(layer, ((W - layer.width) // 2, 1150 - layer.height // 2))
    if seg.get("sub"):
        sub = _mask_money(seg["sub"])
        sf = fit(sub, W6, 44, 900)
        tw = sf.getlength(sub)
        d.rounded_rectangle((W // 2 - tw / 2 - 26, 1238, W // 2 + tw / 2 + 26, 1306), 14, fill=(15, 15, 20, 175))
        d.text((W // 2, 1272), sub, font=sf, fill=(255, 255, 255), anchor="mm")
    if seg.get("day") is not None:
        stext(d, (56, 1430), _mask_money(spec["note"]), font(W6, 28), sw=5)

    # 巻き戻しの飾り（走査線と◀◀）
    if seg.get("rewind"):
        for y in range(0, H, 6):
            d.line((0, y, W, y), fill=(0, 0, 0, 40), width=2)
        # ◀◀ はフォントに無いので三角形を描く
        for k in range(2):
            x = W - 90 - k * 62
            tri = [(x, 520), (x, 600), (x - 62, 560)]
            d.polygon(tri, fill=(255, 255, 255), outline=(20, 20, 20), width=5)
    return im


def seg_at(segs, t):
    i = 0
    for k, s in enumerate(segs):
        if s["start"] <= t:
            i = k
    return i


def compose(segs, spec, frames_dir, total, out_silent):
    nframes = int(round(total * FPS))
    have = {int(p.stem.split("_")[1]): p for p in frames_dir.glob("f_*.jpg")}
    # 巻き戻しに使う「もしも」のフレーム
    ideal_frames = sorted(f for f in have
                          if segs[seg_at(segs, (f - 1) / FPS)].get("grade") == "ideal")
    proc = subprocess.Popen([FFMPEG, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                             "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                             "-crf", "18", "-preset", "medium", str(out_silent)], stdin=subprocess.PIPE)
    last = None
    for n in range(nframes):
        t = n / FPS
        i = seg_at(segs, t)
        seg = segs[i]
        if seg.get("rewind"):
            k = (t - seg["start"]) / max(1e-6, seg["end"] - seg["start"])
            idx = ideal_frames[max(0, int(round((1 - k) * (len(ideal_frames) - 1))))]
            base = Image.open(have[idx]).convert("RGB")
            base = ImageEnhance.Color(base).enhance(0.6)
            # 横ずれのノイズ
            off = int(12 * ((n * 7) % 5 - 2))
            base = base.transform(base.size, Image.AFFINE, (1, 0, off, 0, 1, 0))
        else:
            fp = have.get(n + 1)
            base = Image.open(fp).convert("RGB") if fp else last
            base = grade(base, seg.get("grade"), t - seg["start"])
        last = base if not seg.get("rewind") else last
        fr = base.convert("RGBA")
        fr.alpha_composite(overlay(t, segs, spec, i))
        proc.stdin.write(fr.convert("RGB").tobytes())
        if n % 150 == 0:
            print(f"  合成 {n}/{nframes}", flush=True)
    proc.stdin.close()
    proc.wait()


def finish(segs, silent, wav, total, out, bgm=None):
    cmd = [FFMPEG, "-y", "-loglevel", "error", "-i", str(silent), "-i", str(wav)]
    parts = ["[1:a]apad[narr]"]
    mix = ["[narr]"]
    idx = 2
    cta = next(s for s in segs if s.get("cta"))
    st = cta["voice_start"]
    cmd += ["-i", str(LINE_OV)]
    li = idx
    idx += 1
    parts.append(f"[{li}:v]chromakey={CHROMA}:0.10:0.05,setpts=PTS-STARTPTS+{st:.6f}/TB[lov]")
    parts.append(f"[0:v][lov]overlay=0:0:eof_action=pass:enable='gte(t,{st - 0.5 / FPS:.6f})'[v]")
    ms = int(st * 1000)
    parts.append(f"[{li}:a]adelay={ms}|{ms},volume=0.3[lsnd]")
    mix.append("[lsnd]")
    if bgm:
        cmd += ["-stream_loop", "-1", "-i", str(bgm)]
        parts.append(f"[{idx}:a]volume=0.12[bgm]")
        mix.append("[bgm]")
        idx += 1
    parts.append(f"{''.join(mix)}amix=inputs={len(mix)}:normalize=0:duration=first[a]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[v]", "-map", "[a]", "-t", f"{total:.3f}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "19", "-preset", "medium",
            "-c:a", "aac", "-b:a", "192k", str(out)]
    subprocess.run(cmd, check=True)


def main():
    ap = argparse.ArgumentParser(description="もしも型3D動画を書き出す")
    ap.add_argument("spec", nargs="?", help="spec JSON（write_moshimo.py が作る 音声/本番_NNN.3d.json）")
    ap.add_argument("--name", default=None, help="出力名（既定: spec の name）")
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--preview", action="store_true", help="各ショットの静止画だけ")
    ap.add_argument("--skip-render", action="store_true", help="3Dは前回の連番を使う")
    ap.add_argument("--keep-frames", action="store_true", help="完成後も連番を消さない")
    ap.add_argument("--check", action="store_true", help="必要な物が揃っているかだけ見る")
    a = ap.parse_args()
    if a.check:
        probs = check_env()
        for p in probs:
            print("✗", p)
        print("OK" if not probs else "NG")
        sys.exit(1 if probs else 0)
    if not a.spec:
        ap.error("spec を指定してください")
    probs = check_env()
    if probs:
        sys.exit("3Dで作れません: " + " / ".join(probs))

    sweep_work()
    t_all = time.time()
    spec = json.loads(Path(a.spec).read_text(encoding="utf-8"))
    name = a.name or spec["name"]
    work = WORK / name
    work.mkdir(parents=True, exist_ok=True)
    print("──① ナレーション", flush=True)
    wav, segs, narr_total = narrate(spec, work)
    cta = next(s for s in segs if s.get("cta"))
    total = max(narr_total, cta["voice_start"] + ffprobe_dur(LINE_OV) + 0.2)
    segs[-1]["end"] = total
    pace_walks(segs)
    for s in segs:
        print(f"  {s['start']:6.2f}-{s['end']:6.2f}  {s['scene']:6s} {s['hero']['act']:5s} {s.get('big', '')}")
    print(f"ナレーション {narr_total:.1f}秒 / 動画 {total:.1f}秒", flush=True)
    if total < MIN_SECONDS and not a.preview:
        sys.exit(f"尺が {total:.1f}秒 で1分に届きません（台本を足してください）")
    frames = work / ("preview" if a.preview else "frames")
    if not a.skip_render:
        print("──② 3D書き出し（1時間前後）", flush=True)
        render_3d(segs, spec, total, frames, a.preview)
    if a.preview:
        files = sorted(frames.glob("preview_*.jpg"))
        tiles = []
        for fp in files:
            i = int(fp.stem.split("_")[1])
            t = (segs[i]["start"] + segs[i]["end"]) / 2
            base = grade(Image.open(fp).convert("RGB"), segs[i].get("grade"), 0).convert("RGBA")
            base.alpha_composite(overlay(t, segs, spec, i))
            tiles.append(base.convert("RGB").resize((270, 480)))
        cols = 6
        rows = (len(tiles) + cols - 1) // cols
        sheet = Image.new("RGB", (270 * cols, 480 * rows), (0, 0, 0))
        for k, tl in enumerate(tiles):
            sheet.paste(tl, ((k % cols) * 270, (k // cols) * 480))
        sheet.save(work / "preview_sheet.jpg", quality=90)
        print("[確認用]", work / "preview_sheet.jpg")
        return
    print("──③ 字幕・カウンターを重ねる", flush=True)
    silent = work / "video_silent.mp4"
    compose(segs, spec, frames, total, silent)
    print("──④ 音を重ねて2本に", flush=True)
    outdir = Path(a.out_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    tk_tmp, yt_tmp = work / f"{name}_TikTok.mp4", work / f"{name}_YouTube.mp4"
    finish(segs, silent, wav, total, tk_tmp)
    bgm = find_bgm(BGM_DIR)                     # 旧形式と同じ選び方
    finish(segs, silent, wav, total, yt_tmp, bgm=bgm)
    # 2本とも出来てから置き場へ移す（途中で落ちた時に半端な mp4 を投稿させない）
    tk, yt = outdir / tk_tmp.name, outdir / yt_tmp.name
    os.replace(tk_tmp, tk)
    os.replace(yt_tmp, yt)
    (work / "segments.json").write_text(json.dumps(segs, ensure_ascii=False, indent=1), encoding="utf-8")
    if not a.keep_frames:
        shutil.rmtree(frames, ignore_errors=True)
        silent.unlink(missing_ok=True)
    print(f"[出力] {tk}")
    print(f"[出力] {yt}")
    print(f"完了 {total:.1f}秒の動画 / 所要 {(time.time() - t_all) / 60:.0f}分")


if __name__ == "__main__":
    main()
