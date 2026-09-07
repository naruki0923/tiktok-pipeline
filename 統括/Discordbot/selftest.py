#!/usr/bin/env python3
"""起動前の自己診断。Botトークンが無くても実行できる範囲を全部見る。

    ./.venv/bin/python selftest.py

「準備できてない項目」を先に潰すためのもの。ここが全部✔なら、あとは
Discordでメッセージを送るだけで動くはず。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import config
import runner

ok_all = True


def check(label: str, ok: bool, hint: str = "") -> None:
    global ok_all
    print(f"{'✔' if ok else '✘'} {label}" + ("" if ok else f"  → {hint}"))
    ok_all = ok_all and ok


def voicevox_up() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:50021/version", timeout=3) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def llm_check() -> tuple[bool, str]:
    """経路名だけでなく、実際に1回叩いて生きているか確かめる。

    2026-09-05: `which claude` が通るだけで「claude-cli」と表示していたため、
    CLIのOAuthセッションが切れていても selftest は素通りし、毎朝6時の自動実行が
    台本の手前で落ちるまで気づけなかった。
    """
    sys.path.insert(0, str(config.GEN_SCRIPTS))
    try:
        import llm  # noqa: PLC0415
        return llm.check()
    except Exception as e:  # noqa: BLE001
        return False, f"読めません({e})"


def main() -> None:
    print("── 機密 ──")
    check("DISCORD_BOT_TOKEN", bool(config.BOT_TOKEN), ".env に入れる（README参照）")
    print(f"  通知先チャンネル: {config.CHANNEL_ID or '未指定（最初に話しかけた所を使う）'}")
    # bot._auto と同じ優先順位で見る。state.json の auto が .env の既定を上書きするので、
    # config.AUTO_TIME をそのまま出すと実際の実行時刻と食い違う（2026-09-05に18:00と誤表示）。
    auto = {"enabled": config.AUTO_ENABLED, "time": config.AUTO_TIME}
    auto.update(runner.load_state().get("auto", {}))
    print(f"  自動実行: {'オン' if auto['enabled'] else 'オフ'} / 毎日 {auto['time']}")
    weekly = {"enabled": config.WEEKLY_ENABLED, "dow": config.WEEKLY_DOW,
              "time": config.WEEKLY_TIME}
    weekly.update(runner.load_state().get("weekly", {}))
    print(f"  週次レビュー: {'オン' if weekly['enabled'] else 'オフ'} / "
          f"毎週{'月火水木金土日'[int(weekly['dow']) % 7]}曜 {weekly['time']}")

    print("\n── 各工程のスクリプト ──")
    for label, p in [
        ("1_リサーチ auto_research.py", config.AUTO_RESEARCH),
        ("2_台本生成 use_ref.py", config.USE_REF),
        ("2_台本生成 write_script.py", config.WRITE_SCRIPT),
        ("2_台本生成 venv", config.GEN_PY),
        ("3_動画生成 venv", config.VIDEO_PY),
        ("4_投稿 venv", config.POST_PY),
        ("5_分析 weekly_review.py", config.WEEKLY_REVIEW),
    ]:
        check(label, Path(p).exists(), f"{p} が見つからない")

    print("\n── 外部ツール ──")
    check("ffmpeg", bool(shutil.which("ffmpeg")), "brew install ffmpeg")
    codex = shutil.which("codex")
    check("Codex CLI", bool(codex), "Codexをインストールする")
    if codex:
        p = subprocess.run([codex, "login", "status"], capture_output=True, text=True)
        login = "\n".join((p.stdout, p.stderr))
        check("CodexのChatGPTログイン", p.returncode == 0 and "ChatGPT" in login,
              "codex login でChatGPTアカウントにログインする（APIキーは使わない）")
    check("VOICEVOX ENGINE (50021)", voicevox_up(), "VOICEVOX.app を起動する")
    check("背景動画（外付けSSD）",
          Path("/Volumes/Extreme SSD/素材/動画・画像素材/4K 散歩動画").exists(),
          "外付けSSD「Extreme SSD」を繋ぐ")
    ok_llm, detail = llm_check()
    check(f"台本生成のLLM経路（{detail}）", ok_llm,
          "ターミナルで `claude` を起動し /login でログインし直す")

    print("\n── 投稿の認証 ──")
    check("TikTokログインセッション", (config.POST_SCRIPTS / ".chrome-profile").exists(),
          "4_投稿/scripts で login.py を1回だけ実行")
    check("YouTube OAuthトークン", (config.POST_SCRIPTS / "youtube_token.json").exists(),
          "4_投稿/scripts で youtube_auth.py を1回だけ実行")

    print("\n── discord.py ──")
    try:
        import discord  # noqa: PLC0415
        check(f"discord.py {discord.__version__}", True)
    except ImportError:
        check("discord.py", False, "pip install -r requirements.txt")

    print("\n── 直近の状態 ──")
    print(f"  完成済みの最新: {runner.latest_ready() or 'なし'}")
    scripts = sorted(config.SCRIPT_TXT_DIR.glob("本番_*.txt"))
    print(f"  台本: {len(scripts)}本（最新 {scripts[-1].stem if scripts else 'なし'}）")

    print("\n" + ("🟢 準備OK。./run.sh で起動できます" if ok_all
                  else "🟡 ✘ の項目を潰してから起動してください"))
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
