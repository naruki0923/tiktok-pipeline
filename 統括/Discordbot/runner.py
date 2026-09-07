"""既存パイプライン（3_動画生成 / 4_投稿）を subprocess で呼ぶ薄いラッパ。

Bot はここ経由でしか各工程を触らない。工程スクリプト自体は変更しない
（手動運用のフォールバックを常に残す方針）。

LINEbot/runner.py からの変更点は2つ:
  ・make_video に **--title-lines を必ず渡す**（LINE版はこれが抜けていてフックがバラけた）
  ・TikTok投稿のキャプションに **本番_NNN_TikTok.txt**（短い形式）を優先して使う
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

import config

# 投稿スロット→予約時刻。**TikTokとYouTubeで時刻が違う**（2026-08-05 社長指示で
# TikTokの朝だけ8:30へ。YouTubeは6:30のまま）。1日2本運用（朝／夜＝18:30）。
SLOTS = {
    "am": {"tiktok": "08:30", "youtube": "06:30"},
    "pm": {"tiktok": "18:30", "youtube": "18:30"},
}
SLOT_WHEN = {"am": "翌朝", "pm": "夜"}
MAX_TITLE_LINES = 3   # タイトルテロップに統合できる上限（台本作成ルール.md）


def slot_time(slot: str, where: str) -> str:
    """その媒体の予約時刻（HH:MM）。"""
    return SLOTS.get(slot, SLOTS["am"]).get(where, SLOTS.get(slot, SLOTS["am"])["youtube"])


def slot_label(slot: str, where: str | None = None) -> str:
    """人に見せる予約時刻。媒体で違うので where を渡せば片方だけを出す。

    where 未指定（＝両方まとめて言う時）は、時刻が違えば両方を併記する。
    「翌朝6:30に出したつもりがTikTokは8:30」と食い違って見えないようにするため。
    """
    when, times = SLOT_WHEN.get(slot, "翌朝"), SLOTS.get(slot, SLOTS["am"])
    if where in times:
        return f"{when}{times[where]}"
    if len(set(times.values())) == 1:
        return f"{when}{next(iter(times.values()))}"
    return f"{when}（TikTok {times['tiktok']}／YouTube {times['youtube']}）"


def _slot_cli(slot: str, where: str) -> list[str]:
    """スロットを post_*.py 共通の --schedule-time / --schedule-days に変換。

    am: 翌日のその時刻。pm: 次の18:30（その時刻まで15分未満なら翌日へ送る。
    TikTokは直近すぎる予約を弾くため）。
    """
    time_str = slot_time(slot, where)
    hh, mm = (int(x) for x in time_str.split(":"))
    if slot == "pm":
        now = datetime.now()
        cutoff = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        days = 0 if now < cutoff - timedelta(minutes=15) else 1
    else:
        days = 1
    return ["--schedule-time", time_str, "--schedule-days", str(days)]


# 工程ログのうち、末尾に無くても必ず残す行（警告と各工程の判定結果）。
_KEEP_LINE = re.compile(r"^(⚠️|❌|✅|\s*\[(BGM|音量|予約|保存)\])")


def _trim_log(text: str, limit: int = 1800) -> str:
    """ログを縮める。ただし序盤の警告行は末尾に関係なく残す。

    単純な ``text[-1800:]`` だと、**序盤に出るBGM・キャプションの警告が落ちる**。
    2026-08-20〜09-04のTikTok投稿は15本ともBGM無しだったが、`manual_fixups` が
    見るのはこの縮めたログなので「BGM追加を確認できず」が届かず、Discordに
    ⚠️が一度も出ないまま公開され続けた（2026-09-05に発覚）。
    """
    if len(text) <= limit:
        return text
    tail = text[-limit:]
    head = text[: len(text) - len(tail)]
    kept = [ln for ln in head.splitlines() if _KEEP_LINE.match(ln)]
    return ("\n".join(kept) + "\n…（中略）…\n" + tail) if kept else tail


def _run(cmd: list[str], cwd: Path, timeout: int) -> tuple[bool, str]:
    """コマンドを実行し (成否, ログ末尾) を返す。

    **caffeinate で包む**。無人実行（既定06:30）はMacが寝ている時間帯で、
    darkwakeの隙にBotが起きて動画生成を始めても途中で再びスリープに入る。
    子プロセスは止まるがtimeoutは実時間で進むので、2026-08-08の本番_040は
    音声だけできた状態で「タイムアウト（2400秒）」になった。
    """
    cmd = ["/usr/bin/caffeinate", "-i", "-m", "-s", *cmd]
    # capture_output のパイプ先では Python の print がブロックバッファリングされる。
    # タイムアウト時にも停止工程を特定できるよう、子Pythonの途中ログを即時に流す。
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    try:
        p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                           timeout=timeout, env=env)
    except subprocess.TimeoutExpired as e:
        stdout = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        stderr = e.stderr.decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        log = _trim_log(stdout + stderr)
        return False, f"タイムアウト（{timeout}秒）" + (f"\n{log}" if log else "")
    except OSError as e:
        return False, f"起動できません: {e}"
    log = (p.stdout or "") + (p.stderr or "")
    return p.returncode == 0, _trim_log(log)


# --- パス ------------------------------------------------------------------
def script_txt(name: str) -> Path:
    return config.SCRIPT_TXT_DIR / f"{name}.txt"


def tiktok_mp4(name: str) -> Path:
    return config.OUTPUT_DIR / f"{name}_TikTok.mp4"


def youtube_mp4(name: str) -> Path:
    return config.OUTPUT_DIR / f"{name}_YouTube.mp4"


def caption_txt(name: str, platform: str = "youtube") -> Path:
    """キャプション。TikTokは短い専用ファイルがあればそれを使う。"""
    if platform == "tiktok":
        tk = config.CAPTION_DIR / f"{name}_TikTok.txt"
        if tk.exists():
            return tk
    return config.CAPTION_DIR / f"{name}.txt"


# --- 状態（title_lines 等の持ち回り） --------------------------------------
def load_state() -> dict:
    if config.STATE.exists():
        try:
            return json.loads(config.STATE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_state(s: dict) -> None:
    config.STATE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def remember(name: str, **fields) -> None:
    """動画1本ぶんのメタ（title_lines・角度・参考URL）を残す。"""
    s = load_state()
    s.setdefault("videos", {}).setdefault(name, {}).update(fields)
    save_state(s)


def title_lines_for(name: str) -> int:
    """--title-lines に渡す値。state に無ければ整形済み台本から計算する。

    整形済み台本は「句点＝空行」なので、先頭の空行までが1文目＝フック。
    2文目以降は含めない（台本作成ルール.md／上限3）。
    """
    saved = load_state().get("videos", {}).get(name, {}).get("title_lines")
    if isinstance(saved, int) and saved > 0:
        return min(saved, MAX_TITLE_LINES)
    txt = script_txt(name)
    if not txt.exists():
        return 1
    block = 0
    for ln in txt.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            break
        block += 1
    return min(max(block, 1), MAX_TITLE_LINES)


# --- 動画生成 ---------------------------------------------------------------
# フック1文目の行数。過去18本すべて2行、修正後の036が3行。**1行は起こり得ない**
# （＝台本が句読点なしの改行で書かれているサイン。本番_036で実際に起きた）。
HOOK_MIN_LINES, HOOK_MAX_LINES = 2, 3


def hook_block(name: str) -> list[str]:
    """整形済み台本の先頭ブロック＝フックの1文目（句点が空行になっている）。"""
    txt = script_txt(name)
    if not txt.exists():
        return []
    block = []
    for ln in txt.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            break
        block.append(ln.strip())
    return block


def title_problems(name: str, title_lines: int | None = None) -> list[str]:
    """タイトルカードが壊れる条件を機械的に弾く。問題が無ければ空リスト。

    compose_video は**文の境界を無視して先頭 title_lines 行**を1枚にまとめるので、
    ここがズレると必ず事故る。ありうる壊れ方は2つで、どちらもここで捕まえる:
      ・title_lines が小さい → フックがバラけて1行だけのタイトルになる（本番_036）
      ・title_lines が大きい → 2文目（保存煽り）がタイトルに混ざる（本番_032）
    """
    block = hook_block(name)
    if not block:
        return [f"台本が読めない/空: {name}.txt"]
    k = len(block)
    tl = title_lines if title_lines is not None else title_lines_for(name)
    probs = []
    if k < HOOK_MIN_LINES:
        probs.append(
            f"フックの1文目が{k}行しかありません（過去作は全部2〜3行）。"
            f"台本が句読点なしの改行で書かれている疑いがあります → 「{block[0]}」")
    elif k > HOOK_MAX_LINES:
        probs.append(
            f"フックの1文目が{k}行あります（タイトルカードは3行まで）。"
            f"読点を減らすか、文を2つに分けてください")
    want = min(k, MAX_TITLE_LINES)
    if tl != want:
        probs.append(f"--title-lines が {tl} ですが、1文目は{k}行なので {want} であるべきです")
    return probs


def make_video(name: str) -> tuple[bool, str]:
    """台本 name.txt から TikTok/YouTube 2本を生成。VOICEVOX+SSDが前提。

    レンダリング前に必ず title_problems() で止める。**タイトルは一番壊れやすく、
    壊れたまま出来上がると人が気づくまで分からない**ので、作らせない方が安全。
    """
    txt = script_txt(name)
    if not txt.exists():
        return False, f"台本が無い: {txt.name}"
    tl = title_lines_for(name)
    probs = title_problems(name, tl)
    if probs:
        return False, ("🚫 タイトルが壊れるので生成を止めました\n"
                       + "\n".join(f"・{p}" for p in probs)
                       + f"\n\n現在のフック1文目:\n" + "\n".join(f"  {b}" for b in hook_block(name)))
    started = time.time()
    ok, log = _run(
        [str(config.VIDEO_PY), "make_video.py", str(txt), "--title-lines", str(tl)],
        cwd=config.VIDEO_SCRIPTS,
        timeout=2400,     # 生成は数分〜。余裕を持って40分
    )
    if not ok and log.startswith("タイムアウト"):
        ok2, log2 = _wait_for_render(name, started)
        if ok2:
            return True, log2
        log = log + "\n" + log2
    return ok, log


def _wait_for_render(name: str, started: float, grace: int = 5400) -> tuple[bool, str]:
    """タイムアウト後、レンダリングが本当に死んだのかを見に行く。

    タイムアウトの正体は**Macのスリープ**であることが多い（2026-08-08 本番_040、
    2026-08-14 本番_046）。caffeinate を噛ませても、06:30の無人実行は darkwake の
    隙に始まって途中でまた寝るので、子プロセスは止まったまま timeout の実時間だけ進む。
    しかも subprocess.run が殺すのは caffeinate だけで、**ffmpeg は生き残って
    起床後に完成する**（046はタイムアウト報告の3時間半後に出来上がっていた）。
    なので「タイムアウト＝失敗」と即断せず、mp4 が揃うまで grace ぶん待つ。
    """
    want = [tiktok_mp4(name), youtube_mp4(name)]
    deadline = time.time() + grace
    while time.time() < deadline:
        if all(p.exists() and p.stat().st_mtime >= started for p in want):
            time.sleep(20)   # 書き込み完了を待つ
            mins = int((time.time() - started) / 60)
            return True, f"（スリープで中断した様子。{mins}分かかりましたが2本とも完成しています）"
        time.sleep(60)
    return False, "動画も出来ていません（本当に失敗）"


def manual_fixups(log: str) -> list[str]:
    """post_tiktok.py のログから、押す前に人が直す必要がある点を拾う。

    どれも致命傷ではないので処理は続くが、黙って流すと予約日が今日のままだったり
    BGM無しのまま公開されたりする（2026-08-25 本番_055）。Discordに出して気づかせる。
    """
    notes = []
    if "日付 設定失敗" in log or "予約設定に不備" in log:
        notes.append("⚠️ **予約日が入っていません**（今日のままの可能性）。押す前に日付を直してください")
    if "BGM追加を確認できず" in log or "楽曲行が見つからない" in log:
        notes.append("⚠️ **BGMが入っていません**。編集画面で手動追加してください")
    if "キャプション欄が見つかりません" in log or "再入力しても内容が指定と一致" in log:
        notes.append("⚠️ **キャプションが入っていません**。画面で確認してください")
    return notes


# --- 投稿 -------------------------------------------------------------------
def post_tiktok(name: str, auto: bool = False, slot: str = "am") -> tuple[str, str]:
    """TikTokへ予約投稿。(状態, ログ) を返す。

    状態は3つ。**半自動でも「設定できた」と「実際に押された」は別物**なので、
    真偽値ではなく状態で返す（成功と誤報告しないための区別）。
      confirmed … 【投稿予約する】まで確定した
      awaiting  … 設定は完了。確定ボタンは人間待ち（Chromeは開いたまま）
      failed    … 途中で失敗

    auto=False（既定）は社長がChromeで自分で押す運用。押せない時だけ auto=True。
    """
    mp4, cap = tiktok_mp4(name), caption_txt(name, "tiktok")
    if not mp4.exists():
        return "failed", f"動画が無い: {mp4.name}"
    if not cap.exists():
        return "failed", f"キャプションが無い: {cap.name}"
    cmd = [str(config.POST_PY), "post_tiktok.py", str(mp4),
           "--caption-file", str(cap), "--yes", *_slot_cli(slot, "tiktok")]
    if auto:
        cmd.append("--auto")
    ok, log = _run(cmd, cwd=config.POST_SCRIPTS, timeout=1200)
    if not ok:
        return "failed", log
    # post_tiktok.py は確定できた時だけ「確定しました」「確定を確認しました」と出す。
    # どちらもTikTok Studioのコンテンツ一覧に載ったのを見てから出す文言で、
    # 画面遷移だけを根拠にした「確定を検知」は誤報告の元だったので使わない。
    # 半自動で10分待っても押されなかった場合も終了コードは0なので、ログで見分ける。
    if "確定しました" in log or "確定を確認" in log:
        return "confirmed", log
    return ("failed" if auto else "awaiting"), log


def post_youtube(name: str, slot: str = "am") -> tuple[bool, str]:
    """YouTube Shortsへ予約公開（公式API）。"""
    mp4, cap = youtube_mp4(name), caption_txt(name, "youtube")
    if not mp4.exists():
        return False, f"動画が無い: {mp4.name}"
    if not cap.exists():
        return False, f"キャプションが無い: {cap.name}"
    return _run(
        [str(config.POST_PY), "youtube_upload.py", str(mp4),
         "--caption-file", str(cap), *_slot_cli(slot, "youtube")],
        cwd=config.POST_SCRIPTS, timeout=1200,
    )


# --- プレビュー -------------------------------------------------------------
def preview_mp4(name: str) -> Path | None:
    """Discordに添付できるサイズまで落としたプレビューを作る。

    本番動画は70〜80MBありDiscordの添付上限を超えるので、540x960・低ビットレートに
    再エンコードして 8MB 以内に収める。中身の確認用なので画質は割り切る。
    """
    src = tiktok_mp4(name)
    if not src.exists():
        return None
    out = config.CACHE / f"{name}_preview.mp4"
    if out.exists() and out.stat().st_mtime >= src.stat().st_mtime:
        return out
    p = subprocess.run(
        ["ffmpeg", "-y", "-i", str(src),
         "-vf", "scale=540:-2", "-c:v", "libx264", "-preset", "veryfast",
         "-b:v", "700k", "-maxrate", "900k", "-bufsize", "1400k",
         "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", str(out)],
        capture_output=True, text=True, timeout=900,
    )
    if p.returncode != 0 or not out.exists():
        return None
    if out.stat().st_size > config.PREVIEW_MAX_MB * 1024 * 1024:
        return None   # それでも大きいなら添付を諦める（bot側でポスターに切り替える）
    return out


def poster_jpg(name: str, at: float = 1.2) -> Path | None:
    """タイトルカードが写るフレーム。**毎回これをDiscordに添付して目視確認できるようにする。**

    冒頭0秒ちょうどはフェードやSEで読みにくいので少し後ろ（既定1.2秒）を取る。
    動画を再生しなくても1枚見ればタイトルの崩れが分かる。
    """
    src = tiktok_mp4(name)
    if not src.exists():
        return None
    out = config.CACHE / f"{name}_title.jpg"
    if out.exists() and out.stat().st_mtime >= src.stat().st_mtime:
        return out
    subprocess.run(["ffmpeg", "-y", "-ss", str(at), "-i", str(src),
                    "-frames:v", "1", "-q:v", "3", str(out)],
                   capture_output=True, timeout=120)
    return out if out.exists() else None


def latest_ready() -> str | None:
    """投稿できる状態の最新の動画名（TikTok版mp4がある中で番号が一番大きいもの）。"""
    names = sorted(p.stem.replace("_TikTok", "") for p in config.OUTPUT_DIR.glob("本番_*_TikTok.mp4"))
    return names[-1] if names else None
