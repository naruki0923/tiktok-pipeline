"""工程エラーを、ChatGPTログイン済みのCodex CLIに調査・修正させる。

OpenAI APIキーは使わない。`codex login status` が ChatGPT ログインを
示さない環境では、API課金へ意図せず切り替わるのを防ぐため実行しない。
"""
from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime

import config


def _chatgpt_login() -> tuple[bool, str]:
    codex = shutil.which("codex")
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
    logged_in, detail = _chatgpt_login()
    if not logged_in:
        return False, detail

    codex = shutil.which("codex")
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

    if summary_file.exists():
        summary = summary_file.read_text(encoding="utf-8").strip()
    else:
        summary = (p.stderr or p.stdout or "結果を取得できませんでした").strip()[-3000:]
    return p.returncode == 0, summary or "Codexの報告は空でした"
