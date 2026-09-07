"""cloudflared クイックトンネルを起動し、公開URL(https://xxx.trycloudflare.com)を得る。

アカウント不要・ポート開放不要でMacのローカルサーバを外部公開できる。URLは
起動ごとに変わるが、server.py が起動時に line_client.set_webhook_endpoint で
LINE側へ自動登録するので、社長がコンソールに貼り直す必要はない。
"""
from __future__ import annotations

import re
import subprocess
import threading
import time

_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def start(port: int, timeout: float = 30.0) -> tuple[subprocess.Popen, str]:
    """トンネルを起動し (プロセス, 公開URL) を返す。URL取得失敗時は例外。"""
    proc = subprocess.Popen(
        ["cloudflared", "tunnel", "--url", f"http://localhost:{port}",
         "--no-autoupdate"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    url_box: dict[str, str] = {}

    def _reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            if "url" not in url_box:
                m = _URL_RE.search(line)
                if m:
                    url_box["url"] = m.group(0)

    threading.Thread(target=_reader, daemon=True).start()

    deadline = time.time() + timeout
    while time.time() < deadline:
        if "url" in url_box:
            return proc, url_box["url"]
        if proc.poll() is not None:
            raise RuntimeError("cloudflared が起動直後に終了した")
        time.sleep(0.3)

    proc.terminate()
    raise TimeoutError("トンネルURLの取得がタイムアウトした")
