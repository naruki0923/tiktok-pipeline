#!/usr/bin/env python3
"""3Dオフィス・ライブ監視盤のローカルサーバー。

統括/パイプライン状態.json と実ファイルの有無を読み、/state でJSONを返す。
dashboard.html（Canvas 3Dオフィス）が3秒ごとにポーリングして反映する。
読み取り専用（パイプラインには一切書き込まない）。標準ライブラリのみ。

起動:  python3 統括/ダッシュボード/server.py   → http://localhost:8756
"""
from __future__ import annotations

import json
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent          # 統括/ダッシュボード
ROOT = HERE.parents[1]                          # プロジェクトルート
STATE = ROOT / "統括" / "パイプライン状態.json"
PORT = 8756

ORDER = ["research", "script", "video", "post", "analyze"]
PM_TEXT = {
    "research": "リサーチに指示中",
    "script": "台本化を監督中",
    "video": "動画生成を監督中",
    "post": "投稿設定へ（確定は社長）",
    "analyze": "分析を監督中",
    "done": "1本 完了 🎉",
}
STAGE_LABEL = {
    "research": "1. リサーチ", "script": "2. 台本生成", "video": "3. 動画生成",
    "post": "4. 投稿", "analyze": "5. 分析", "done": "完了",
}


def fresh(p: Path, hours: float = 24) -> bool:
    try:
        return (time.time() - p.stat().st_mtime) < hours * 3600
    except OSError:
        return False


def build_state() -> dict:
    st = {}
    if STATE.exists():
        try:
            st = json.loads(STATE.read_text(encoding="utf-8"))
        except ValueError:
            st = {}
    no = st.get("video_no", "?")
    stage = st.get("stage", "idle")

    refs = list((ROOT / "2_台本生成" / "文字起こし").glob("ref_*.txt"))
    files = {
        "ref": any(fresh(r) for r in refs),
        "script": (ROOT / "2_台本生成" / f"台本_{no}.txt").exists(),
        "fmt": (ROOT / "3_動画生成" / "音声" / f"本番_{no}.txt").exists(),
        "mp4": (ROOT / "3_動画生成" / "output" / f"本番_{no}_TikTok.mp4").exists(),
        "post": (ROOT / "4_投稿" / "投稿予定" / f"本番_{no}.txt").exists(),
        "rep": False,
    }

    board = [
        [f"本番_{no}", STAGE_LABEL.get(stage, stage), "#f0b35a" if stage in ORDER else "#82d69c"],
        ["状態更新", (st.get("updated", "") or "—").replace("T", " ")[5:16], "#8b97a4"],
    ]
    # 完成物の実数もボードへ
    outs = len(list((ROOT / "3_動画生成" / "output").glob("本番_*_TikTok.mp4")))
    board.append(["完成動画（累計）", f"{outs}本", "#82d69c"])

    return {
        "video": no,
        "stage": stage,
        "idx": ORDER.index(stage) if stage in ORDER else (5 if stage == "done" else -1),
        "pm": PM_TEXT.get(stage, "待機中"),
        "files": files,
        "board": board,
        "ts": time.strftime("%H:%M:%S"),
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(HERE), **kw)

    def do_GET(self):
        if self.path.startswith("/state"):
            body = json.dumps(build_state(), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path in ("/", ""):
            self.path = "/dashboard.html"
        super().do_GET()

    def log_message(self, *a):  # 静かに
        pass


if __name__ == "__main__":
    print(f"AIオフィス監視盤: http://localhost:{PORT}  (root={ROOT})")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
