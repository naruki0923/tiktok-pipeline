#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
背景動画＋ナレーション音声＋縦書きテロップ を合成して、
TikTok用（BGMなし）と YouTube用（BGMあり）の2本を書き出す。

前提:
  - voicevox_tts.py で <name>.wav と <name>.timing.json を生成済み
  - 背景動画（散歩動画）を1つ指定
  - ffmpeg があること
  - テロップ描画に telop_render.py（同じフォルダ）＋ Pillow(.venv)

使い方:
  ./.venv/bin/python compose_video.py \
      --audio ../音声/narration_001.wav \
      --timing ../音声/narration_001.timing.json \
      --bg "/Volumes/Extreme SSD/素材/動画・画像素材/4K 散歩動画/【4K】平日の新宿をのんびり散歩【Tokyo Walk】.mp4" \
      --out-dir ../output --name 動画_test_001 \
      --bgm ../assets/BGM/*.mp3 \
      --red-lines 1
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

import telop_render

W, H = 1080, 1920
FPS = 30

# 金額・お金に関するテロップの判定（このときは金額SEを入れる）
# 実際に「金額」を言っている文だけに限定する。給付/手当 等の一般語だけでは鳴らさない
# （お金の話でないのに金額SEが乗るのを防ぐ）。数字＋金額単位、または年収/月収/いくら 等のみ。
MONEY_RE = re.compile(r"\d+\s*(万|億|千|円|ドル|%|％)|年収|月収|時給|日給|いくら|何円|全額|半額")
# 「いいね・保存」オーバーレイを出す文の判定
LIKE_RE = re.compile(r"いいね|保存")
# 「LINE誘導」オーバーレイを出す文（CTA）の判定。無ければ最後の文に出す
LINE_RE = re.compile(r"個別面談|面談|プロフィール|リンク|説明会")

# 文の感情でSEを選び分けるための判定（ユーザー指定ルール）。優先順: 危機感 → ネガティブ → いいこと。
# ・危機感を煽る文 → 警告音1
CRISIS_RE = re.compile(r"申請しないと|やらないと|放置|二度と|表示されません|今すぐ|手遅れ|見逃|危険")
# ・ネガティブな文 → チーン1 / チリン（基本は使わない。倒産・差押え等の強い負イベントだけ）
NEG_RE = re.compile(r"倒産|差し押さえ|差押|滞納|自己破産|借金|給料未払い|給与未払い|踏み倒")
# ・いいこと（ポジティブ）を言う文 → きらーん（基本は使わず、良い話のときだけ）
POS_RE = re.compile(r"もらえ|受け取れ|補助|立て替え|タダ|お得|高くな|たくさんあり|安心|助か")

# 「Nつ目」のテロップを丸数字に置き換える（①②…）。テロップ表示のみ・音声はそのまま。
CIRCLED = {"1": "①", "2": "②", "3": "③", "4": "④", "5": "⑤", "6": "⑥", "7": "⑦", "8": "⑧", "9": "⑨",
           "１": "①", "２": "②", "３": "③", "４": "④", "５": "⑤",
           "一": "①", "二": "②", "三": "③", "四": "④", "五": "⑤"}
ITEM_RE = re.compile(r"^([0-9０-９一二三四五六七八九])\s*つ目(?:は)?[、。]?$")

# オーバーレイのグリーンバック色（素材共通）
CHROMA = "0x73D44C"
# 音声フォーマット統一（amix 用）
AFMT = "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo"


def is_money_text(text):
    return bool(MONEY_RE.search(text))


def is_crisis_text(text):
    return bool(CRISIS_RE.search(text))


def is_negative_text(text):
    return bool(NEG_RE.search(text))


def is_positive_text(text):
    return bool(POS_RE.search(text))


def apply_telop_display_rules(telops):
    """テロップの「表示」を整えるための後処理（音声・タイミングは変えない）。
    ① 「Nつ目」のテロップは丸数字（①〜）にして、直後のテロップ（項目名）と1枚に統合する。
       例: 「1つ目」＋「失業手当」→「①失業手当」（表示期間は両方ぶんに広げる）。
    ② 「いいね・保存」を促すテロップは、直前のテロップと1枚に統合し、改行（\\n）でつなぐ。
       例: 「忘れないよう」＋「いいねと保存をしてください」→ 1枚のテロップ（2列）。
    どちらも統合後のテロップは sf は元のまま／ef は後ろのテロップに合わせて連続表示を保つ。"""
    # ① 「Nつ目」→ 丸数字 ＋ 次のテロップと統合
    merged = []
    i = 0
    while i < len(telops):
        tp = telops[i]
        m = ITEM_RE.match(tp["text"].strip())
        if m and i + 1 < len(telops):
            nxt = telops[i + 1]
            num = CIRCLED.get(m.group(1), m.group(1))
            t = dict(tp)
            t["text"] = num + nxt["text"]
            t["ef"] = nxt["ef"]
            t["red"] = None
            merged.append(t)
            i += 2
            continue
        merged.append(tp)
        i += 1

    # ② 「いいね・保存」テロップを直前のテロップと統合（改行でつなぐ・同じ文のときだけ）
    out = []
    for tp in merged:
        if LIKE_RE.search(tp["text"]) and out and out[-1]["sent"] == tp["sent"]:
            prev = out[-1]
            prev["text"] = prev["text"] + "\n" + tp["text"]
            prev["ef"] = tp["ef"]
        else:
            out.append(tp)
    return out


def snap(t):
    """時刻をフレーム境界に量子化（テロップと背景カットをフレーム単位で一致させるため）。"""
    return round(t * FPS) / FPS


def render_telops(telops, tmpdir):
    """各テロップのPNGを生成し、パスのリストを返す。telop["red"] があれば赤で表示。"""
    paths = []
    for i, tp in enumerate(telops):
        out = os.path.join(tmpdir, f"telop_{i:03d}.png")
        telop_render.render_telop(tp["text"], out, red=tp.get("red"), canvas=(W, H))
        paths.append(out)
    return paths


def ffprobe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nk=1:nw=1", path],
        capture_output=True, text=True,
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def build_continuous_telops(lines, total_f, title_lines=0, red_lines=1):
    """各テロップを「開始フレーム〜次テロップの開始フレーム」で表示（空白の瞬間をなくす）。
    すべて整数フレームで扱い、背景カットとフレーム単位で一致させる。
    title_lines>0 のとき、冒頭 title_lines 行を1枚のタイトルテロップにまとめる（複数列＝3行までOK）。"""
    n = len(lines)
    sfs = [round(ln["start"] * FPS) for ln in lines]
    seen_sents = set()

    def sent_of(j):
        return lines[j].get("sent", 0)

    telops = []
    i = 0
    if title_lines > 0 and n > 0:
        k = min(title_lines, n)
        # 各行を列として並べる（\n で列区切り）。1行目（フック）を赤にする
        text = "\n".join(lines[j]["text"] for j in range(k))
        ef = sfs[k] if k < n else total_f
        s = sent_of(0)
        telops.append({"text": text, "sf": sfs[0], "ef": ef, "red": lines[0]["text"],
                       "sent": s, "sent_start": True})
        seen_sents.add(s)
        i = k
    while i < n:
        sf = sfs[i]
        ef = sfs[i + 1] if i + 1 < n else total_f
        red = lines[i]["text"] if (title_lines == 0 and i < red_lines) else None
        s = sent_of(i)
        telops.append({"text": lines[i]["text"], "sf": sf, "ef": ef, "red": red,
                       "sent": s, "sent_start": s not in seen_sents})
        seen_sents.add(s)
        i += 1
    return telops


def plan_scene_intervals(telops, total_f, scene_secs):
    """場面転換点をテロップの開始フレームの中から選ぶ（約 scene_secs 間隔）。
    転換フレーム＝テロップ切り替えフレームなので、背景とテロップが1フレームもズレない。"""
    step = round(scene_secs * FPS)
    cuts = [telops[0]["sf"]]     # 0
    last = telops[0]["sf"]
    for tp in telops:
        if tp["sf"] - last >= step:
            cuts.append(tp["sf"])
            last = tp["sf"]
    bounds = cuts + [total_f]
    return [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]  # (開始F, 終了F)


def build_scene_sources(intervals, bgs, seg_starts, speed):
    """各場面に背景クリップ・開始位置・尺(フレーム)を割り当てる。"""
    scenes = []
    for i, (af, bf) in enumerate(intervals):
        clip = bgs[i % len(bgs)]
        tl_frames = bf - af
        src_len = (tl_frames / FPS) * speed        # 1.2倍速にする分、長めに切り出す
        if seg_starts and i < len(seg_starts):
            start = seg_starts[i]
        else:
            dur = ffprobe_duration(clip)
            frac = [0.15, 0.45, 0.75, 0.30, 0.60, 0.90][i % 6]
            start = max(0.0, min(frac * dur, max(0.0, dur - src_len)))
        scenes.append({"clip": clip, "start": round(start, 2),
                       "tl_frames": tl_frames, "src_len": src_len})
    return scenes


def sentence_texts(lines):
    """文番号 → その文の本文（連結）"""
    out = {}
    for ln in lines:
        s = ln.get("sent", 0)
        out[s] = out.get(s, "") + ln["text"]
    return out


def plan_se_events(telops, lines, se_dir, opening_se, neutral_ses, money_ses,
                   skip_sents=(), warn_se=None, warn_sents=(),
                   neg_ses=None, pos_se=None, seed=20260715):
    """SEを鳴らす (時刻, ファイルパス) のリストを作る。1文につき1回だけ。
    文の内容（感情）に応じてSEを選ぶ。優先順:
    ・冒頭（最初の文）        : opening_se（和太鼓ドドン）
    ・いいね促しの文(warn_sents): warn_se（警告音＝危機感）
    ・金額の文               : money_ses（レジスター＋金額）
    ・危機感を煽る文          : warn_se（警告音）
    ・ネガティブな文          : neg_ses（チーン1 / チリン）
    ・いいことを言う文        : pos_se（きらーん。基本は使わず良い話のときだけ）
    ・それ以外              : neutral_ses からランダム
    どのケースでも「直前に鳴らしたSEと同じものは選ばない」（同じSEを連続させない）。
    ・skip_sents の文はSEを鳴らさない（オーバーレイ素材が自前の音を持つため）。
    時刻はテロップの開始フレーム(sf)なので、テロップと1フレームもズレない。"""
    import random
    sent_text = sentence_texts(lines)
    neutral = [s for s in (neutral_ses or []) if s]
    neg_ses = [s for s in (neg_ses or []) if s]
    rng = random.Random(seed)

    events = []
    last_file = None  # 直前に鳴らしたSEファイル名（連続回避用）

    def pick_neutral():
        cands = [s for s in neutral if s != last_file] or neutral
        return rng.choice(cands) if cands else None

    def pick(cands):
        """候補を前から見て「直前と違う最初のもの」を返す。全部ダメならニュートラルから。"""
        for c in cands:
            if c and c != last_file:
                return c
        return pick_neutral()

    for idx, tp in enumerate(telops):
        if not tp.get("sent_start") or tp["sent"] in skip_sents:
            continue
        t = tp["sf"] / FPS
        txt = sent_text.get(tp["sent"], "")
        chosen = None
        if idx == 0:
            chosen = opening_se or pick_neutral()
        elif tp["sent"] in warn_sents:
            chosen = warn_se or pick_neutral()
        elif is_money_text(txt):
            # 金額SEは2音同時（レジスター＋金額）。連続回避の対象外（お金の特別演出）
            for m in money_ses:
                events.append((t, os.path.join(se_dir, m)))
            if money_ses:
                last_file = money_ses[-1]
            continue
        elif is_crisis_text(txt):
            chosen = pick([warn_se] + neg_ses)          # 危機感 → 警告音（無理ならネガ）
        elif is_negative_text(txt):
            chosen = pick(neg_ses)                       # ネガティブ → チーン/チリン
        elif is_positive_text(txt):
            chosen = pick([pos_se])                      # いいこと → きらーん
        else:
            chosen = pick_neutral()                      # その他 → ランダム
        if chosen:
            events.append((t, os.path.join(se_dir, chosen)))
            last_file = chosen
    events = [(t, p) for t, p in events if os.path.exists(p)]
    events.sort(key=lambda e: e[0])
    return events


def find_overlay_frames(telops, lines, total_f):
    """いいね保存 / LINE誘導 を出す開始フレームを決める。
    ・いいね保存: 「いいね/保存」を含む**その行(テロップ)**の開始フレームから、
                  **次の文が始まるフレーム**まで（次の文に行くときにカット）
    ・LINE誘導 : CTA（個別面談 等）を含む文、無ければ最後の文の先頭テロップの開始フレーム
    戻り値: dict（見つからないキーは無し）"""
    sent_text = sentence_texts(lines)
    res = {}

    # いいね保存: キーワードを含むテロップ行そのもの
    for tp in telops:
        if LIKE_RE.search(tp["text"]):
            like_sent = tp["sent"]
            # 次の文の先頭フレーム＝カット位置
            end_f = total_f
            for t2 in telops:
                if t2["sent"] > like_sent:
                    end_f = t2["sf"]
                    break
            res["like"] = {"sf": tp["sf"], "end_f": end_f, "sent": like_sent}
            break

    # LINE誘導: CTAキーワードを含む「その行(テロップ)」に乗せる（文全体ではなく行単位）。
    # これで「無料の個別面談に〜」の行にだけオーバーレイが乗り、その手前の
    # 「申請方法を〜」「フォローして」の行はテロップを残せる。
    # 該当行が無ければ最後の文の先頭テロップにフォールバック。
    line_tp = last_start = None
    for tp in telops:
        if LINE_RE.search(tp["text"]):
            line_tp = tp                       # 後ろの方を優先（CTAは終盤）
        if tp.get("sent_start"):
            last_start = tp
    tp = line_tp or last_start
    if tp is not None:
        res["line"] = {"sf": tp["sf"], "sent": tp["sent"], "text": tp["text"]}
    return res


def build_filter(scenes, speed, telops, has_bgm, se_times, se_volume=0.25, ov=None, pad_dur=0.0,
                 ov_volume=0.3):
    """ffmpeg の filter_complex を組み立てる。
    入力順: 場面0..(N-1) / テロップ / ナレーション / BGM / SE0..SEk / オーバーレイ(いいね保存, LINE画像, 指差し)
    ov: {"like":{"start","dur"}, "line":{"start"}} 形式。該当キーがあればそのオーバーレイを重ねる。
    pad_dur: ナレーション後に無音を足す秒数（LINE誘導の単独表示用）。"""
    ov = ov or {}
    parts = []
    labels = ""
    for i, sc in enumerate(scenes):
        # 各場面: 9:16化 → speed倍速 → 場面尺ぴったり(end_frame)でトリム（フレーム単位でカット一致）
        parts.append(
            f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,"
            f"setpts=(PTS-STARTPTS)/{speed},fps={FPS},trim=end_frame={sc['tl_frames']},setpts=PTS-STARTPTS[sc{i}]"
        )
        labels += f"[sc{i}]"
    parts.append(f"{labels}concat=n={len(scenes)}:v=1:a=0[bg]")

    cur = "bg"
    base = len(scenes)
    for j, tp in enumerate(telops):
        # 閾値はフレームの0.5手前に置き、フレームsf以降ef未満で表示（浮動小数の境界ブレを排除）
        s = (tp["sf"] - 0.5) / FPS
        e = (tp["ef"] - 0.5) / FPS
        parts.append(
            f"[{cur}][{base + j}:v]overlay=0:0:enable='gte(t,{s:.6f})*lt(t,{e:.6f})'[v{j}]"
        )
        cur = f"v{j}"

    # --- オーバーレイ（テロップの上に重ねる）---
    # 入力インデックスは 場面/テロップ/ナレーション/BGM/SE の後ろから順に割り当てる
    ov_idx = len(scenes) + len(telops) + 1 + (1 if has_bgm else 0) + len(se_times)

    # 素材はすでに正しい大きさ・位置で作られているので、緑を抜いてそのまま(0:0)重ねるだけ。
    # similarity は 0.25 未満にすること（chromakeyは色差のみで判定するため、
    # 大きくすると白黒（無彩色）のアイコンまで透過してしまう）
    ov_audio = []      # (入力index, 開始秒) … 素材の音声もミックスする
    for key, label in (("like", "likeov"), ("line", "lineov")):
        if key not in ov:
            continue
        st, du = ov[key]["start"], ov[key]["dur"]
        # setpts で素材の時間軸を st までずらす。これをしないと素材は0秒から再生され、
        # 表示する頃には再生し終わって静止画のように見えてしまう。
        parts.append(
            f"[{ov_idx}:v]chromakey={CHROMA}:0.10:0.05,setpts=PTS-STARTPTS+{st:.6f}/TB[{label}]"
        )
        parts.append(
            f"[{cur}][{label}]overlay=0:0:eof_action=pass:enable='gte(t,{st - 0.5 / FPS:.6f})"
            f"*lt(t,{st + du - 0.5 / FPS:.6f})'[v_{key}]"
        )
        cur = f"v_{key}"
        if ov[key].get("has_audio"):
            ov_audio.append((ov_idx, st))
        ov_idx += 1

    video_label = cur

    # 音声: ナレーション ＋ BGM ＋ SE群 を normalize=0 で足し込む（各音量は維持）
    narr_idx = len(scenes) + len(telops)
    a_labels = []
    pad = f",apad=pad_dur={pad_dur:.3f}" if pad_dur > 0 else ""
    parts.append(f"[{narr_idx}:a]{AFMT}{pad}[a_narr]")
    a_labels.append("[a_narr]")
    next_idx = narr_idx + 1
    if has_bgm:
        parts.append(f"[{next_idx}:a]volume=0.12,{AFMT}[a_bgm]")
        a_labels.append("[a_bgm]")
        next_idx += 1
    for k, t in enumerate(se_times):
        ms = int(round(t * 1000))
        parts.append(f"[{next_idx + k}:a]adelay={ms}:all=1,volume={se_volume},{AFMT}[a_se{k}]")
        a_labels.append(f"[a_se{k}]")

    # オーバーレイ素材が持つ音（アニメーションの効果音）も、開始位置に合わせてミックス
    for n, (in_idx, st) in enumerate(ov_audio):
        ms = int(round(st * 1000))
        parts.append(f"[{in_idx}:a]adelay={ms}:all=1,volume={ov_volume},{AFMT}[a_ov{n}]")
        a_labels.append(f"[a_ov{n}]")

    if len(a_labels) == 1:
        parts.append("[a_narr]anull[aout]")
    else:
        # normalize=0 で音量維持 → 音割れ防止にリミッター
        parts.append(
            f"{''.join(a_labels)}amix=inputs={len(a_labels)}:normalize=0:duration=first[amixed]"
        )
        parts.append("[amixed]alimiter=limit=0.95[aout]")
    return ";".join(parts), video_label, "aout"


def run_compose(audio, timing, bgs, seg_starts, scene_secs, speed, out_path, red_lines,
                title_lines=0, bgm=None, se_dir=None, opening_se=None, neutral_ses=None,
                money_ses=None, se_volume=0.25, warn_se=None, neg_ses=None, pos_se=None,
                like_overlay=None, line_overlay=None, ov_volume=0.3):
    with open(timing, encoding="utf-8") as f:
        data = json.load(f)
    total = data["total"]
    total_f = round(total * FPS)
    lines = data["lines"]

    telops = build_continuous_telops(lines, total_f, title_lines=title_lines, red_lines=red_lines)
    # テロップの表示を整える（「Nつ目」→①統合、いいね文を1枚に統合）。音声・タイミングは不変。
    telops = apply_telop_display_rules(telops)

    # オーバーレイの開始フレーム（テロップの切り替わりに1フレームも狂わず合わせる）
    hits = find_overlay_frames(telops, lines, total_f)
    ov = {}
    skip_sents = set()
    warn_sents = set()
    if like_overlay and os.path.exists(like_overlay) and "like" in hits:
        h = hits["like"]
        # 素材の尺いっぱい、ただし次の文が始まったらカット
        dur = min(ffprobe_duration(like_overlay), (h["end_f"] - h["sf"]) / FPS)
        ov["like"] = {"start": h["sf"] / FPS, "dur": dur, "has_audio": True}
        # この文（「あなたのおすすめには〜」）の頭では必ず警告音を鳴らす。
        # いいね素材の音は「忘れないように〜」の行から鳴るので時間がずれ、両立する。
        warn_sents.add(h["sent"])
    if line_overlay and os.path.exists(line_overlay) and "line" in hits:
        h = hits["line"]
        line_sf = h["sf"]
        line_ef = line_sf + round(ffprobe_duration(line_overlay) * FPS)
        ov["line"] = {"start": line_sf / FPS, "dur": ffprobe_duration(line_overlay), "has_audio": True}
        # LINE誘導オーバーレイが乗る「その行」のテロップを消す。
        # 同じ文の「申請方法を〜」「フォローして」の行はテロップを残す。
        # ただし、LINE誘導がそのテロップの表示区間を最後まで覆えない場合は消さない
        # （覆えない“尾”でテロップもLINE誘導も無い＝背景だけの時間ができるのを防ぐ）。
        telops = [tp for tp in telops
                  if not (tp["sent"] == h["sent"] and tp["text"] == h.get("text")
                          and line_ef >= tp["ef"])]
        # 念のため：LINE誘導開始の直前に隙間があれば、直前テロップを伸ばして必ず埋める
        prev = max((tp for tp in telops if tp["sf"] < line_sf),
                   key=lambda x: x["sf"], default=None)
        if prev is not None and prev["ef"] < line_sf:
            prev["ef"] = line_sf

    # LINE誘導が最後まで再生しきるよう、必要なら動画を延長
    out_total = total
    if "line" in ov:
        out_total = max(total, ov["line"]["start"] + ov["line"]["dur"])
    pad_dur = max(0.0, out_total - total)
    out_total_f = round(out_total * FPS)

    intervals = plan_scene_intervals(telops, out_total_f, scene_secs)
    scenes = build_scene_sources(intervals, bgs, seg_starts, speed)

    se_events = []
    if se_dir:
        se_events = plan_se_events(telops, lines, se_dir,
                                   opening_se, neutral_ses, money_ses or [],
                                   skip_sents=skip_sents, warn_se=warn_se, warn_sents=warn_sents,
                                   neg_ses=neg_ses, pos_se=pos_se)

    with tempfile.TemporaryDirectory() as tmp:
        telop_imgs = render_telops(telops, tmp)

        cmd = ["ffmpeg", "-y", "-v", "error", "-stats"]
        for sc in scenes:
            # フレーム不足を防ぐため 1 秒多めに読み込む（後段の trim=end_frame で正確に切り詰める）
            cmd += ["-stream_loop", "-1", "-ss", f"{sc['start']}", "-t", f"{sc['src_len'] + 1.0:.3f}", "-i", sc["clip"]]
        for p in telop_imgs:
            cmd += ["-i", p]
        cmd += ["-i", audio]
        has_bgm = bool(bgm)
        if has_bgm:
            cmd += ["-stream_loop", "-1", "-i", bgm]
        for _, se_path in se_events:
            cmd += ["-i", se_path]

        # オーバーレイ素材（build_filter の ov と同じ順で入力を並べる）
        if "like" in ov:
            cmd += ["-i", like_overlay]
        if "line" in ov:
            cmd += ["-i", line_overlay]

        filt, vlabel, alabel = build_filter(scenes, speed, telops, has_bgm,
                                            [t for t, _ in se_events], se_volume=se_volume,
                                            ov=ov, pad_dur=pad_dur, ov_volume=ov_volume)
        cmd += [
            "-filter_complex", filt,
            "-map", f"[{vlabel}]", "-map", f"[{alabel}]",
            "-t", f"{out_total:.3f}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "192k",
            out_path,
        ]
        desc = " → ".join(f"{os.path.basename(sc['clip'])}({sc['tl_frames']/FPS:.1f}s)" for sc in scenes)
        print(f"[合成] {out_path}\n  背景{len(scenes)}場面({speed}倍速・約{scene_secs}s毎・テロップ境界に整合): {desc}\n  {len(telops)}テロップ / {total:.1f}秒 / SE {len(se_events)}個")
        subprocess.run(cmd, check=True)
        print(f"[出力] {out_path}")


def main():
    ap = argparse.ArgumentParser(description="背景＋音声＋テロップを合成して2本書き出す")
    ap.add_argument("--audio", required=True, help="ナレーション音声(wav/mp3)")
    ap.add_argument("--timing", required=True, help="タイミングJSON")
    ap.add_argument("--bg", required=True, action="append",
                    help="背景動画（散歩動画）。複数指定可（場面ごとに使い分ける）")
    ap.add_argument("--scene-secs", type=float, default=15.0,
                    help="背景1場面のおおよその長さ（秒）。この間隔でテロップ境界に合わせて場面転換する（既定15）")
    ap.add_argument("--speed", type=float, default=1.2, help="背景の再生速度（既定1.2倍）")
    ap.add_argument("--seg-starts", default=None,
                    help="各場面の開始秒をカンマ区切りで指定（例: 60,300,600）。前進している部分を選ぶ用")
    ap.add_argument("--out-dir", default="../output", help="出力フォルダ")
    ap.add_argument("--name", default="動画_001", help="出力ベース名")
    ap.add_argument("--bgm", default=None, help="BGM（YouTube版に埋め込む）")
    ap.add_argument("--red-lines", type=int, default=1, help="先頭から赤にする行数（フック）")
    ap.add_argument("--title-lines", type=int, default=0,
                    help="冒頭の何行を1枚のタイトルテロップにまとめるか（0=しない）。タイトルは複数列＝3行までOK")
    ap.add_argument("--se-dir", default="../assets/SE", help="効果音フォルダ")
    ap.add_argument("--opening-se", default="和太鼓ドドン.mp3", help="冒頭（最初の文）のSE")
    ap.add_argument("--neutral-se",
                    default="シャン(万能・強調orツッコミ).mp3,小鼓（こつづみ）.mp3,"
                            "拍子木1.mp3,拍子木2.mp3,和太鼓でカカッ.mp3",
                    help="感情が特に無い文の頭で鳴らすSE（カンマ区切りプール）。ここからランダムで選び、"
                         "同じSEは連続させない。空文字で無効")
    ap.add_argument("--money-se", default="レジスターで精算.mp3,金額表示.mp3",
                    help="金額の文で鳴らすSE（カンマ区切り。2音同時）")
    ap.add_argument("--neg-se", default="チーン1.mp3,チリン.mp3",
                    help="ネガティブな文で鳴らすSE（カンマ区切り。直前と違う方を選ぶ）")
    ap.add_argument("--pos-se", default="",
                    help="いいことを言う文で鳴らすSE（良い話のときだけ。空文字＝使わず、ニュートラルからランダム）")
    ap.add_argument("--warn-se", default="警告音1.mp3",
                    help="いいね促しの文・危機感を煽る文の頭で鳴らすSE")
    ap.add_argument("--se-volume", type=float, default=0.15,
                    help="SEの音量（1.0=原音。既定0.15≒-16dB）")
    ap.add_argument("--no-se", action="store_true", help="SEを一切入れない")
    # オーバーレイ（アニメーション＋効果音入りのグリーンバック素材。そのまま0:0に重ねる）
    ap.add_argument("--like-overlay", default="../テンプレート/いいね・保存.mp4",
                    help="いいね・保存のオーバーレイ動画。「いいね/保存」を含む文の頭から再生")
    ap.add_argument("--line-overlay", default="../テンプレート/LINE誘導.mp4",
                    help="LINE誘導のオーバーレイ動画。CTA（個別面談 等）の文の頭から再生")
    ap.add_argument("--ov-volume", type=float, default=0.3,
                    help="オーバーレイ素材が持つ音の音量（既定0.3）")
    ap.add_argument("--no-overlay", action="store_true", help="オーバーレイを一切入れない")
    ap.add_argument("--tiktok-only", action="store_true", help="TikTok用のみ書き出す")
    args = ap.parse_args()

    for clip in args.bg:
        if not os.path.exists(clip):
            print(f"[エラー] 背景動画が見つかりません: {clip}\n  （外付けSSDが接続されているか確認）", file=sys.stderr)
            sys.exit(1)
    os.makedirs(args.out_dir, exist_ok=True)

    seg_starts = None
    if args.seg_starts:
        seg_starts = [float(x) for x in args.seg_starts.split(",")]

    se_dir = None if args.no_se else args.se_dir
    opening_se = args.opening_se or None
    neutral_ses = [s for s in args.neutral_se.split(",") if s] if args.neutral_se else []
    money_ses = [m for m in args.money_se.split(",") if m] if args.money_se else []
    neg_ses = [s for s in args.neg_se.split(",") if s] if args.neg_se else []
    pos_se = args.pos_se or None

    ov_kw = {}
    if not args.no_overlay:
        ov_kw = dict(like_overlay=args.like_overlay, line_overlay=args.line_overlay,
                     ov_volume=args.ov_volume)

    # TikTok用（BGMなし）
    tiktok_out = os.path.join(args.out_dir, f"{args.name}_TikTok.mp4")
    run_compose(args.audio, args.timing, args.bg, seg_starts, args.scene_secs, args.speed,
                tiktok_out, args.red_lines, title_lines=args.title_lines, bgm=None,
                se_dir=se_dir, opening_se=opening_se, neutral_ses=neutral_ses, money_ses=money_ses,
                se_volume=args.se_volume, warn_se=args.warn_se, neg_ses=neg_ses, pos_se=pos_se, **ov_kw)

    # YouTube用（BGMあり）
    if not args.tiktok_only and args.bgm and os.path.exists(args.bgm):
        yt_out = os.path.join(args.out_dir, f"{args.name}_YouTube.mp4")
        run_compose(args.audio, args.timing, args.bg, seg_starts, args.scene_secs, args.speed,
                    yt_out, args.red_lines, title_lines=args.title_lines, bgm=args.bgm,
                    se_dir=se_dir, opening_se=opening_se, neutral_ses=neutral_ses, money_ses=money_ses,
                    se_volume=args.se_volume, warn_se=args.warn_se, neg_ses=neg_ses, pos_se=pos_se, **ov_kw)
    elif not args.tiktok_only and args.bgm:
        print(f"[警告] BGMが見つからないため YouTube版はスキップ: {args.bgm}", file=sys.stderr)


if __name__ == "__main__":
    main()
