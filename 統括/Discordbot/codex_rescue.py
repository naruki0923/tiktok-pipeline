"""工程エラーを、ChatGPTログイン済みのCodex CLIに調査・修正させる。

OpenAI APIキーは使わない。`codex login status` が ChatGPT ログインを
示さない環境では、API課金へ意図せず切り替わるのを防ぐため実行しない。

**どのcodexを呼ぶかが壊れやすい**。使うモデルは ~/.codex/config.toml から
読まれるが、そこを書いているのはChatGPT.app（自分で更新する）で、PATH上の
codex（Homebrew等・人が上げないと古いまま）とは別物。版がズレると
「そのモデルには新しいCodexが要る」で毎回400になる。しかも**その時でも
codexの終了コードは0**なので、返り値だけ見ていると成功と区別が付かない。
2026-09-10はこれで救援が6回とも即死し、誰も気づかないまま同じ失敗を繰り返した。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import config

# ChatGPT.app が同梱しているcodex。アプリが自分で更新するので、たいていPATHのより新しい。
APP_CODEX = Path("/Applications/ChatGPT.app/Contents/Resources/codex")


def _version(codex: str) -> tuple[int, ...]:
    """`codex --version` の数字。取れなければ空タプル（＝一番古い扱い）。"""
    try:
        p = subprocess.run([codex, "--version"], capture_output=True,
                           text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return ()
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", (p.stdout or "") + (p.stderr or ""))
    return tuple(int(x) for x in m.groups()) if m else ()


def codex_bin() -> tuple[str, tuple[int, ...]]:
    """使うcodexと、その版を返す。無ければ ("", ())。

    **PATHのものとChatGPT.app同梱の、新しい方**を採る。モデルを決めるのは
    アプリ側の config.toml なので、CLIだけ古いと動かない。同点ならPATHを優先
    （人が意図して入れたものを尊重する）。
    """
    cands: list[tuple[tuple[int, ...], int, str]] = []
    path_codex = shutil.which("codex")
    if path_codex:
        cands.append((_version(path_codex), 1, path_codex))
    if APP_CODEX.exists():
        cands.append((_version(str(APP_CODEX)), 0, str(APP_CODEX)))
    if not cands:
        return "", ()
    ver, _, best = max(cands)
    return best, ver


# 「動いたように見えて実は何もしていない」印。codexは400でも終了コード0を返す。
_FAILED = re.compile(r'^ERROR: \{|requires a newer version of Codex'
                     r'|"type"\s*:\s*"(invalid_request_error|error)"', re.M)


def _why_failed(text: str, codex: str, ver: tuple[int, ...]) -> str | None:
    """出力が「実は動いていない」時だけ、短い理由を返す。"""
    if not _FAILED.search(text or ""):
        return None
    shown = ".".join(str(x) for x in ver) or "版不明"
    if "requires a newer version of Codex" in text:
        model = (re.search(r"The '([^']+)' model requires", text) or [None, "?"])[1]
        return (f"Codex CLIが古くてモデル `{model}` を使えません"
                f"（使ったのは `{codex}` v{shown}）。\n"
                "`brew upgrade codex` で上げるか、ChatGPT.appを最新にしてください。")
    msg = re.search(r'"message"\s*:\s*"([^"]+)"', text)
    return (f"Codexがエラーを返しました（`{codex}` v{shown}）: "
            f"{msg.group(1) if msg else text.strip()[-300:]}")


def _chatgpt_login(codex: str) -> tuple[bool, str]:
    if not codex:
        return False, "codex CLIが見つかりません"
    try:
        p = subprocess.run([codex, "login", "status"], capture_output=True,
                           text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"Codexのログイン状態を確認できません: {e}"
    status = "\n".join(x for x in (p.stdout, p.stderr) if x).strip()
    if p.returncode != 0 or "ChatGPT" not in status:
        return False, ("CodexがChatGPTログインではないため自動実行しません"
                       f"（APIは使用禁止）: {status or '未ログイン'}")
    return True, status


def _prompt(context: str, error: str) -> str:
    return f"""このTikTok動画自動化プロジェクトでエラーが起きました。
原因を調査し、コードまたはローカル設定で安全に直せる不具合なら修正し、
関連する最小限のテストを実行してください。

発生箇所: {context}
エラー内容:
---
{error[-12000:]}
---

厳守事項:
- OpenAI API、Anthropic APIなどの従量課金APIは使わない。
- TikTok/YouTubeへの投稿、予約、アップロード、Discord送信はしない。
- .env、認証トークン、ブラウザプロファイルなどの機密を表示・変更しない。
- 生成済み動画、台本、採用履歴、state.jsonを削除・巻き戻ししない。
- エラーと無関係な既存変更を消さず、原因に必要な最小範囲だけ直す。
- 外部サービス停止、未接続SSD、VOICEVOX未起動などコードで直せない原因は、
  無理に変更せず必要な人間の操作を明記する。
- 最後に「原因」「変更内容」「確認結果」「人間の操作が必要か」を日本語で簡潔に報告する。
"""


def run(context: str, error: str) -> tuple[bool, str]:
    """Codexを非対話実行し、(正常終了, Discord向け要約) を返す。"""
    if not config.AUTO_CODEX_RESCUE:
        return False, "エラー時のCodex自動起動はオフです"
    codex, ver = codex_bin()
    logged_in, detail = _chatgpt_login(codex)
    if not logged_in:
        return False, detail

    out_dir = config.CACHE / "codex_rescue"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    summary_file = out_dir / f"{stamp}_summary.txt"
    log_file = out_dir / f"{stamp}_run.log"

    # 環境変数にAPIキーがあっても子プロセスへ渡さない。認証は上で確認した
    # ChatGPTログインだけを利用する。
    env = os.environ.copy()
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        env.pop(key, None)

    cmd = [str(codex), "-a", "never", "-s", "workspace-write",
           "-C", str(config.ROOT), "exec", "--skip-git-repo-check",
           "--color", "never", "-o", str(summary_file), "-"]
    try:
        p = subprocess.run(cmd, input=_prompt(context, error), capture_output=True,
                           text=True, timeout=config.CODEX_RESCUE_TIMEOUT, env=env)
        log_file.write_text((p.stdout or "") + "\n" + (p.stderr or ""), encoding="utf-8")
    except subprocess.TimeoutExpired as e:
        stdout = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        stderr = e.stderr.decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        log_file.write_text(stdout + "\n" + stderr, encoding="utf-8")
        return False, f"Codexは{config.CODEX_RESCUE_TIMEOUT // 60}分でタイムアウトしました"
    except OSError as e:
        return False, f"Codexを起動できませんでした: {e}"

    summary = (summary_file.read_text(encoding="utf-8").strip()
               if summary_file.exists()
               else (p.stderr or p.stdout or "結果を取得できませんでした").strip())

    # 終了コード0でも動いていないことがある。**先にそれを見る**。
    # 失敗時の -o の中身は送ったプロンプトのechoで数千字あり、肝心の1行が埋もれる。
    why = _why_failed(summary + "\n" + (p.stdout or "") + "\n" + (p.stderr or ""), codex, ver)
    if why:
        return False, f"🛠 Codexは動けませんでした。\n{why}\n（詳細: `{log_file}`）"
    return p.returncode == 0, summary[-3000:] or "Codexの報告は空でした"
