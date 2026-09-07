"""台本生成に使うLLM呼び出しの1枚レイヤ。

呼び先は2通り。**既定は `claude` CLI のヘッドレス実行**（＝契約済みのClaude Codeを使う。
追加課金なし）。ANTHROPIC_API_KEY を明示的に置いた場合だけ従量課金のAPIに切り替わる。

**呼び出し回数は意図的に絞ってある**: 動画1本につき write_script.py が1回叩くだけ
（季節性判定・台本・キャプションを1回のプロンプトにまとめている）。要所だけLLMを使い、
検索・数値取得・整形・動画化・投稿はすべてスクリプト側で完結させる方針。

    # 任意: 従量課金APIに切り替えたい場合だけ
    export ANTHROPIC_API_KEY=sk-ant-...
    export ANTHROPIC_MODEL=claude-sonnet-5

依存を増やしたくないので anthropic SDK は使わず urllib で直接叩く。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5"
CLI_TIMEOUT = 600


class LLMError(RuntimeError):
    """LLMに繋がらない／空応答。呼び出し側はこれを掴んで通知に回す。"""


def backend() -> str:
    """今どちらの経路を使うか。ログ表示・selftest 用。"""
    if shutil.which("claude") and not os.environ.get("ANTHROPIC_API_KEY"):
        return "claude-cli"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return f"api({os.environ.get('ANTHROPIC_MODEL', DEFAULT_MODEL)})"
    return "none"


def ask(prompt: str, max_tokens: int = 4000) -> str:
    """プロンプトを投げて本文テキストを返す。既定は claude CLI（追加課金なし）。"""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key and shutil.which("claude"):
        return _ask_cli(prompt)
    if key:
        return _ask_api(prompt, key, max_tokens)
    raise LLMError(
        "LLMに繋げません。claude CLI が入っていないなら ANTHROPIC_API_KEY を .env に設定してください。")


def ask_json(prompt: str, max_tokens: int = 2000) -> dict:
    """JSONだけを返させたい用途。```で囲まれても中身を取り出す。"""
    raw = ask(prompt + "\n\nJSONのみを出力してください。前置き・説明は不要です。", max_tokens)
    txt = raw.strip()
    if txt.startswith("```"):
        txt = txt.split("```")[1]
        txt = txt[4:] if txt.startswith("json") else txt
    start, end = txt.find("{"), txt.rfind("}")
    if start < 0 or end < 0:
        raise LLMError(f"JSONが取れませんでした: {raw[:200]}")
    try:
        return json.loads(txt[start:end + 1])
    except json.JSONDecodeError as e:
        raise LLMError(f"JSONの形が壊れています: {e} / {raw[:200]}") from e


def _ask_api(prompt: str, key: str, max_tokens: int) -> str:
    body = json.dumps({
        "model": os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL),
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(API_URL, data=body, headers={
        "content-type": "application/json",
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
    })
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise LLMError(f"Anthropic API {e.code}: {e.read()[:300].decode(errors='replace')}") from e
    except OSError as e:
        raise LLMError(f"Anthropic APIに繋がりません: {e}") from e
    out = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    if not out.strip():
        raise LLMError("Anthropic APIが空の応答を返しました")
    return out


def _ask_cli(prompt: str) -> str:
    """既定の経路。契約済みのClaude Codeを使うので追加課金は発生しない。

    ここで必要なのはテキスト生成だけ。ツールを有効にすると、非対話の ``-p``
    実行中にClaudeが文字数計測などでBashの承認を求め、承認待ちの文章を
    最終回答として返すことがある。ツールを明示的に無効化し、完全なヘッドレス
    呼び出しにする。
    """
    p = subprocess.run(
        ["claude", "-p", "--tools", "", "--no-session-persistence", prompt],
        capture_output=True,
        text=True,
        timeout=CLI_TIMEOUT,
    )
    if p.returncode != 0 or not p.stdout.strip():
        msg = (p.stderr or p.stdout)[:300]
        if _is_auth_error(msg):
            # コードの不具合ではないので、Codexに直させても直らない。
            # 人が再ログインするしかないことを、そのままDiscordに出す（2026-09-05）。
            raise LLMError(
                "claude CLI のログインが切れています（コードの問題ではありません）。"
                "Macのターミナルで `claude` を起動して `/login` でログインし直してください。"
                f"\n確認: claude -p \"1+1は？数字だけ答えて\"\n元のメッセージ: {msg}")
        raise LLMError(f"claude CLI が失敗: {msg}")
    return p.stdout


def _is_auth_error(msg: str) -> bool:
    low = msg.lower()
    return any(k in low for k in
               ("oauth", "authenticate", "unauthorized", "log in", "login", "401"))


def check() -> tuple[bool, str]:
    """実際に1回叩いて経路が生きているか確かめる。selftest.py 用。

    `shutil.which("claude")` が通るだけでは不十分だった。2026-09-04〜05に
    CLIのOAuthセッションが切れていたが selftest は「claude-cli」と表示し続け、
    毎朝6時の自動実行が台本の手前で落ちるまで気づけなかった。
    """
    name = backend()
    if name == "none":
        return False, "LLM経路なし（claude CLI も ANTHROPIC_API_KEY も無い）"
    try:
        out = ask("1+1は？数字だけ答えて", max_tokens=16)
    except LLMError as e:
        return False, f"{name}: {e}"
    return (True, f"{name}: 応答あり") if out.strip() else (False, f"{name}: 空の応答")
