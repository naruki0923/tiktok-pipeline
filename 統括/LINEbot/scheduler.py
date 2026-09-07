"""LINEチャットから設定する自動配信スケジューラ。

社長はMac/cronを一切触らず、LINEで「毎日20時」「自動オフ」と打つだけで
時刻を設定・変更できる。時計はMac上のこのスレッドが持つ（LINE公式の自動配信は
Botに跳ね返らないため）。設定・状態は schedule.json に永続化。
"""
from __future__ import annotations

import datetime
import json
import threading
import time
from typing import Callable

import config

STATE = config.BOT_DIR / "schedule.json"
_DEFAULT = {"enabled": False, "time": "20:00", "done": [], "last_fired": "", "user_id": ""}


def load() -> dict:
    if STATE.exists():
        try:
            return {**_DEFAULT, **json.loads(STATE.read_text())}
        except (json.JSONDecodeError, OSError):
            pass
    return dict(_DEFAULT)


def save(s: dict) -> None:
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=2))


# --- 設定操作（LINEコマンドから呼ぶ） ------------------------------------
def set_enabled(on: bool, t: str | None = None) -> dict:
    s = load()
    s["enabled"] = on
    if t:
        s["time"] = t
    save(s)
    return s


def set_user(uid: str) -> None:
    """push通知先。再起動後もスケジューラが送れるよう永続化。"""
    s = load()
    if uid and s.get("user_id") != uid:
        s["user_id"] = uid
        save(s)


def get_user() -> str:
    return load().get("user_id", "")


def status_text() -> str:
    s = load()
    left = len(remaining_scripts(s))
    return (f"⏰ 自動配信: {'🟢オン' if s['enabled'] else '⚪️オフ'} / 毎日{s['time']}\n"
            f"台本キュー残り: {left}本")


# --- 台本キュー -----------------------------------------------------------
def remaining_scripts(s: dict | None = None) -> list[str]:
    """未処理の台本名（本番_NNN）を番号順で返す。"""
    s = s or load()
    done = set(s.get("done", []))
    names = sorted(p.stem for p in config.SCRIPT_TXT_DIR.glob("本番_*.txt"))
    return [n for n in names if n not in done]


def next_script() -> str | None:
    rem = remaining_scripts()
    return rem[0] if rem else None


def mark_done(name: str) -> None:
    s = load()
    if name not in s["done"]:
        s["done"].append(name)
        save(s)


# --- スケジューラ本体 -----------------------------------------------------
def start(fire_cb: Callable[[], None]) -> None:
    """毎分ちょうどに時刻一致を見て fire_cb を1日1回だけ発火。"""
    def loop() -> None:
        while True:
            s = load()
            if s.get("enabled"):
                now = datetime.datetime.now()
                if now.strftime("%H:%M") == s.get("time") and s.get("last_fired") != now.strftime("%Y-%m-%d"):
                    s["last_fired"] = now.strftime("%Y-%m-%d")
                    save(s)
                    try:
                        fire_cb()
                    except Exception as e:  # 発火失敗でループは止めない
                        print(f"[scheduler] fire error: {e}")
            time.sleep(20)

    threading.Thread(target=loop, daemon=True).start()
