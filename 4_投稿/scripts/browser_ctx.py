#!/usr/bin/env python3
"""ブラウザ起動の共通処理（login.py / post_tiktok.py で共有）

【重要な設計判断】
TikTokは、Playwrightが起動したChromeを自動化ツールとして検知しログインを
ブロックする（起動時に付く --enable-automation 等で navigator.webdriver が立つため）。
実際、Playwright起動のChromeではログイン即「試行回数上限」になる一方、
ユーザーが普通に開いたChromeでは問題なくログインできる。

そこで方式を変更:
  - Chromeを「自動化フラグなし」で普通に起動する（subprocessで直接起動）。
    → TikTokから見て、ユーザーが手で開いたChromeと区別がつかない＝ログイン可能。
  - Playwrightは起動には関与せず、リモートデバッグポート経由で「後から接続」する
    （connect_over_cdp）。接続してもページの navigator.webdriver は立たない。
  - セッションは専用プロファイル(.chrome-profile/)に保存し再利用。

この専用プロファイルは通常のChromeプロファイルとは別物なので普段のブラウジングに影響しない。
"""
from __future__ import annotations

import subprocess
import time
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PROFILE_DIR = SCRIPT_DIR / ".chrome-profile"   # ここにログインセッションが保存される
DEBUG_PORT = 9222
CHROME_BIN = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# 自動化フラグは付けない。付けるとTikTokに検知される。最低限のものだけ。
_CHROME_ARGS = [
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-features=Translate",
]


def _port_ready(port: int = DEBUG_PORT) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


def start_chrome(timeout: int = 20, profile_dir: Path | None = None,
                 port: int = DEBUG_PORT) -> subprocess.Popen:
    """自動化フラグなしのChromeを専用プロファイルで起動し、デバッグポートの応答を待つ。

    戻り値: Popen（呼び出し側で terminate() すること）
    既にポートが空いていれば（前の窓が生きていれば）それを使う。

    profile_dir/port を渡すと別のプロファイルで起動できる。**リサーチ用の
    未ログインChromeを投稿用のログイン済みChromeと分ける**ために使う
    （投稿アカウントでスクレイピングして垢に何かあると面倒なため）。
    """
    profile_dir = profile_dir or PROFILE_DIR
    profile_dir.mkdir(parents=True, exist_ok=True)
    if _port_ready(port):
        # 既に起動済みのChromeがある。新規起動せずそれを使う。
        return None  # type: ignore[return-value]

    proc = subprocess.Popen(
        [CHROME_BIN, f"--user-data-dir={profile_dir}",
         f"--remote-debugging-port={port}", *_CHROME_ARGS],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _port_ready(port):
            return proc
        time.sleep(0.5)
    raise RuntimeError(f"Chromeのデバッグポート({port})が起動しませんでした。")


def connect(p, timeout_ms: int | None = None, port: int = DEBUG_PORT,
            new_page: bool = False):
    """起動済みChromeにPlaywrightから接続。(browser, context, page) を返す。

    new_page=True は、既存タブを人や別処理が閉じる影響を避ける必要がある処理向け。
    ログイン確認など既存タブを使いたい呼び出しは従来どおり False を使う。
    """
    kwargs = {"timeout": timeout_ms} if timeout_ms else {}
    browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", **kwargs)
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    page = context.new_page() if new_page else (
        context.pages[0] if context.pages else context.new_page()
    )
    return browser, context, page


def kill_chrome(port: int = DEBUG_PORT) -> None:
    """デバッグポートを掴んでいるChromeを落とす（応答しなくなった窓の掃除用）。

    まずSIGTERM。止まらなければSIGCONT（停止中だとシグナルを処理できない）を挟んで
    SIGKILL。ここで確実に落とさないと、次の start_chrome がポートを取れず詰まる。
    """
    pat = f"remote-debugging-port={port}"

    def _alive() -> bool:
        # ポートの応答では判定しない。固まったChromeは応答しないのにソケットは
        # 掴んだままなので、「空いた」と誤判定して次の起動がポートを取れなくなる。
        return subprocess.run(["pgrep", "-f", pat],
                              capture_output=True).returncode == 0

    for sig in ("-TERM", "-CONT", "-KILL"):
        subprocess.run(["pkill", sig, "-f", pat], capture_output=True)
        for _ in range(12):     # 各シグナルにつき最大6秒待つ
            if not _alive():
                time.sleep(1.0)   # ソケットが解放されるまで少し待つ
                return
            time.sleep(0.5)


def connect_healthy(p, timeout_ms: int = 25000, profile_dir: Path | None = None,
                    port: int = DEBUG_PORT, new_page: bool = False):
    """接続を試し、駄目ならChromeを入れ替えてもう一度だけ試す。

    **前のセッションで開きっぱなしのChromeは、HTTP(/json)には答えるのに
    WebSocketのアタッチだけ固まることがある**（2026-08-03に18:00の無人実行が
    これで落ちた）。ポートの生存確認だけでは検知できないので、実際に繋いでみて
    駄目なら落として起動し直す。無人実行で人が居ない前提の自己回復。

    戻り値: (browser, context, page, proc)  proc は起動し直した時だけ非None。
    """
    try:
        # start_chrome もこの中。固まったChromeがポートを握っていると
        # 「既に起動済み」と判定できず新規起動に走って、ここで失敗するため。
        proc = start_chrome(profile_dir=profile_dir, port=port)
        return (*connect(p, timeout_ms, port, new_page=new_page), proc)
    except Exception:  # noqa: BLE001 - 種類を問わず「使えない」なら作り直す
        print(f"  ⚠ Chrome({port})が応答しないので起動し直します")
        kill_chrome(port)
        proc = start_chrome(profile_dir=profile_dir, port=port)
        return (*connect(p, timeout_ms, port, new_page=new_page), proc)


def has_session() -> bool:
    """ログイン済みプロファイルが存在しそうかの簡易判定。"""
    return PROFILE_DIR.exists() and any(PROFILE_DIR.iterdir())
