"""既存パイプライン（3_動画生成 / 4_投稿）を subprocess で呼ぶ薄いラッパ。

LINE Botはここ経由でしか各工程を触らない。工程スクリプト自体は一切変更しない
（手動運用のフォールバックを常に残す方針）。
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import config


# 投稿スロット→予約時刻。1日2本運用（朝＝いつも通り／夜＝18:30）。
SLOTS = {
    "am": ("06:30", "翌朝6:30"),   # いつも通り（翌日の朝）
    "pm": ("18:30", "夜18:30"),    # 追加スロット（次の18:30）
}


def slot_label(slot: str) -> str:
    return SLOTS.get(slot, SLOTS["am"])[1]


def _slot_cli(slot: str) -> list[str]:
    """スロットを post_*.py 共通の --schedule-time / --schedule-days に変換。

    am: 翌朝6:30（従来どおり）。
    pm: 次の18:30。今日18:30まで15分以上あれば今日、無ければ翌日にずらす
        （TikTokは直近すぎる予約を弾くため余裕を持たせる）。
    """
    time_str = SLOTS.get(slot, SLOTS["am"])[0]
    hh, mm = (int(x) for x in time_str.split(":"))
    if slot == "pm":
        now = datetime.now()
        cutoff = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        days = 0 if now < cutoff - timedelta(minutes=15) else 1
    else:
        days = 1
    return ["--schedule-time", time_str, "--schedule-days", str(days)]


def _run(cmd: list[str], cwd: Path, timeout: int) -> tuple[bool, str]:
    """コマンドを実行し (成否, ログ末尾) を返す。"""
    try:
        p = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"タイムアウト（{timeout}秒）"
    log = (p.stdout or "") + (p.stderr or "")
    return p.returncode == 0, log[-1500:]


def script_txt(name: str) -> Path:
    """台本テキストのパス（例: 本番_021 → 3_動画生成/音声/本番_021.txt）。"""
    return config.SCRIPT_TXT_DIR / f"{name}.txt"


def caption_txt(name: str) -> Path:
    return config.CAPTION_DIR / f"{name}.txt"


def tiktok_mp4(name: str) -> Path:
    return config.OUTPUT_DIR / f"{name}_TikTok.mp4"


def youtube_mp4(name: str) -> Path:
    return config.OUTPUT_DIR / f"{name}_YouTube.mp4"


# --- 動画生成 -------------------------------------------------------------
def make_video(name: str) -> tuple[bool, str]:
    """台本 name.txt から TikTok/YouTube 2本を生成。VOICEVOX+SSDが前提。"""
    txt = script_txt(name)
    if not txt.exists():
        return False, f"台本が無い: {txt.name}（先にClaudeで台本を作って {config.SCRIPT_TXT_DIR} に置いてね）"
    return _run(
        [str(config.VIDEO_PY), "make_video.py", str(txt)],
        cwd=config.VIDEO_SCRIPTS,
        timeout=1800,  # 生成は数分〜。余裕を持って30分
    )


# --- 投稿 -----------------------------------------------------------------
def post_tiktok(name: str, auto: bool = True, slot: str = "am") -> tuple[bool, str]:
    """TikTokへ予約投稿。LINEのボタン承認を人間ゲートとみなし既定で --auto。

    slot='am'（翌朝6:30・いつも通り）/ 'pm'（夜18:30）。
    """
    mp4, cap = tiktok_mp4(name), caption_txt(name)
    if not mp4.exists():
        return False, f"動画が無い: {mp4.name}"
    if not cap.exists():
        return False, f"キャプションが無い: {cap.name}"
    cmd = [str(config.POST_PY), "post_tiktok.py", str(mp4),
           "--caption-file", str(cap), "--yes", *_slot_cli(slot)]
    if auto:
        cmd.append("--auto")
    return _run(cmd, cwd=config.POST_SCRIPTS, timeout=900)


def post_youtube(name: str, slot: str = "am") -> tuple[bool, str]:
    """YouTube Shortsへ予約公開（公式API）。slot='am'（翌朝6:30）/ 'pm'（夜18:30）。"""
    mp4, cap = youtube_mp4(name), caption_txt(name)
    if not mp4.exists():
        return False, f"動画が無い: {mp4.name}"
    if not cap.exists():
        return False, f"キャプションが無い: {cap.name}"
    return _run(
        [str(config.POST_PY), "youtube_upload.py", str(mp4),
         "--caption-file", str(cap), *_slot_cli(slot)],
        cwd=config.POST_SCRIPTS,
        timeout=900,
    )
