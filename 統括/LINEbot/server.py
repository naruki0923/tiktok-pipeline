"""LINE Bot 本体。

起動すると:
  1. cloudflared トンネルを立てて公開URLを取得
  2. そのURLをLINEのwebhook先として自動登録
  3. Flaskでwebhookを受けてコマンド処理

対応コマンド（LINEにテキストで送る）:
  「021 作って」「生成 021」   → 台本 本番_021.txt から動画生成 → プレビュー＋投稿ボタン
  「投稿 021」                 → 生成済み動画のプレビュー＋投稿ボタン
  「状態」「ヘルプ」            → 使い方

投稿はボタン（＝社長の承認ゲート）を押した時だけ実行する。
"""
from __future__ import annotations

import re
import subprocess
import threading
import time
from pathlib import Path

from flask import Flask, request, abort, send_file

import config
import line_client as line
import runner
import scheduler

app = Flask(__name__)

# 起動時に確定するトンネル公開URL（プレビュー動画の配信元にもなる）
BASE_URL = ""
# push通知の宛先ユーザーID（最初に話しかけた人 or .envで固定）
USER_ID = config.ALLOWED_USER_ID


# --- ヘルパ ---------------------------------------------------------------
def _name_from(text: str) -> str | None:
    """メッセージ中の数字を 本番_NNN に正規化。無ければ None。"""
    m = re.search(r"(\d{1,4})", text)
    if not m:
        return None
    return f"本番_{int(m.group(1)):03d}"


def _poster(name: str) -> Path | None:
    """TikTok動画の先頭フレームからプレビュー用jpgを作る。"""
    src = runner.tiktok_mp4(name)
    if not src.exists():
        return None
    out = config.MEDIA_CACHE / f"{name}.jpg"
    if not out.exists():
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-frames:v", "1", "-q:v", "3", str(out)],
            capture_output=True,
        )
    return out if out.exists() else None


def _preview_messages(name: str) -> list[dict]:
    """プレビュー動画＋投稿ボタンのメッセージ列を組む。"""
    mp4 = runner.tiktok_mp4(name)
    if not mp4.exists():
        return [line.text(f"⚠️ {name} の動画がまだ無いよ。「{name[-3:]} 作って」で生成してね。")]
    poster = _poster(name)
    msgs = []
    if poster and BASE_URL:
        msgs.append(line.video(
            f"{BASE_URL}/media/out/{mp4.name}",
            f"{BASE_URL}/media/cache/{poster.name}",
        ))
    # 1日2本運用：朝（いつも通り6:30）と夜（18:30）のスロットを別々に選べる。
    for slot in ("am", "pm"):
        label = runner.slot_label(slot)
        msgs.append(line.buttons(
            f"{name} の投稿（{label}）", f"✅ {name} 完成", f"{label}に予約：どこに出す？",
            [
                line.postback_action("TikTokに予約", f"tiktok:{slot}:{name}"),
                line.postback_action("YouTubeに予約", f"youtube:{slot}:{name}"),
                line.postback_action("両方に予約", f"both:{slot}:{name}"),
            ],
        ))
    return msgs


# --- 非同期ジョブ ---------------------------------------------------------
def _job_make(name: str) -> None:
    ok, log = runner.make_video(name)
    if not ok:
        line.push(USER_ID, [line.text(f"❌ {name} の生成に失敗\n\n{log[-800:]}")])
        return
    line.push(USER_ID, _preview_messages(name))


def _auto_fire() -> None:
    """スケジューラが時刻に呼ぶ。次の台本を自動生成→プレビューをLINEへ。"""
    uid = USER_ID or scheduler.get_user()
    if not uid:
        print("[auto] 通知先ユーザー未登録のため発火スキップ")
        return
    name = scheduler.next_script()
    if not name:
        line.push(uid, [line.text("🤖(自動) 台本キューが空だよ。Claudeで台本を用意して 音声/ に置いてね")])
        return
    line.push(uid, [line.text(f"🤖(自動) {name} を作るよ（生成中…数分待ってね）")])
    ok, log = runner.make_video(name)
    scheduler.mark_done(name)  # 提案済みとして記録（重複発火を防ぐ）
    if not ok:
        line.push(uid, [line.text(f"❌ {name} の生成に失敗\n\n{log[-700:]}")])
        return
    line.push(uid, _preview_messages(name))


def _job_post(name: str, where: str, slot: str = "am") -> None:
    when = runner.slot_label(slot)
    results = []
    if where in ("tiktok", "both"):
        ok, log = runner.post_tiktok(name, slot=slot)
        results.append(("TikTok", ok, log))
    if where in ("youtube", "both"):
        ok, log = runner.post_youtube(name, slot=slot)
        results.append(("YouTube", ok, log))
    lines = []
    for label, ok, log in results:
        lines.append(f"{'✅' if ok else '❌'} {label}: {f'予約完了（{when}）' if ok else '失敗'}")
        if not ok:
            lines.append(log[-500:])
    line.push(USER_ID, [line.text("\n".join(lines))])


HELP = (
    "📱 使い方\n"
    "＜手動＞\n"
    "・「021 作って」→ 台本から動画を生成\n"
    "・「投稿 021」→ 生成済みを確認して投稿ボタン\n"
    "・1日2本運用：朝(翌朝6:30)と夜(18:30)のボタンを選べる\n"
    "＜自動配信（時間で勝手に走る）＞\n"
    "・「毎日20時」→ その時刻に自動生成→確認が届く\n"
    "・「自動オフ」→ 自動配信を止める\n"
    "・「自動」→ 今の設定を表示\n"
    "※投稿はいつもボタンを押した時だけ（朝6:30 or 夜18:30を選んで予約）\n"
    "※台本(本番_NNN.txt)は事前にClaudeで用意"
)


# --- webhook --------------------------------------------------------------
@app.post("/callback")
def callback():
    body = request.get_data()
    sig = request.headers.get("X-Line-Signature", "")
    if not line.verify_signature(body, sig):
        abort(400)

    global USER_ID
    for ev in request.json.get("events", []):
        uid = ev.get("source", {}).get("userId", "")
        if config.ALLOWED_USER_ID and uid != config.ALLOWED_USER_ID:
            continue  # 許可ユーザー以外は無視
        if not USER_ID and uid:
            USER_ID = uid  # 最初に話しかけた人を通知先に採用
        if uid:
            scheduler.set_user(uid)  # 再起動後も自動配信が届くよう永続化

        etype = ev.get("type")
        token = ev.get("replyToken", "")

        if etype == "message" and ev["message"].get("type") == "text":
            _handle_text(token, ev["message"]["text"])
        elif etype == "postback":
            _handle_postback(token, ev["postback"]["data"])
    return "OK"


def _handle_text(token: str, text: str) -> None:
    # --- 自動配信スケジュールの設定（LINEだけで完結） ---
    if "自動" in text or "毎日" in text:
        if any(k in text for k in ("オフ", "停止", "止め", "切")):
            scheduler.set_enabled(False)
            line.reply(token, [line.text("🛑 自動配信をオフにしたよ")])
            return
        tm = re.search(r"(\d{1,2})\s*[:時]\s*(\d{1,2})?", text)
        t = f"{int(tm.group(1)):02d}:{int(tm.group(2) or 0):02d}" if tm else None
        if t or any(k in text for k in ("オン", "開始", "スタート", "毎日")):
            scheduler.set_enabled(True, t)
            line.reply(token, [line.text("🟢 自動配信をオンにしたよ\n" + scheduler.status_text())])
            return
        line.reply(token, [line.text(scheduler.status_text())])  # 「自動」だけ→状態表示
        return

    name = _name_from(text)
    make_kw = any(k in text for k in ("作", "生成", "つく"))
    post_kw = any(k in text for k in ("投稿", "アップ", "出す"))

    if any(k in text for k in ("ヘルプ", "help", "使い方", "状態")):
        line.reply(token, [line.text(HELP)])
        return
    if name and make_kw:
        line.reply(token, [line.text(f"🎬 {name} 生成開始（数分待ってね）")])
        threading.Thread(target=_job_make, args=(name,), daemon=True).start()
        return
    if name and (post_kw or not make_kw):
        line.reply(token, _preview_messages(name))
        return
    line.reply(token, [line.text(HELP)])


def _handle_postback(token: str, data: str) -> None:
    parts = data.split(":")
    if len(parts) == 3:
        where, slot, name = parts
    else:  # 旧形式 where:name（スロット無し）は朝扱い
        where, _, name = data.partition(":")
        slot = "am"
    if where not in ("tiktok", "youtube", "both") or slot not in ("am", "pm") or not name:
        line.reply(token, [line.text("？ もう一度お願い")])
        return
    dest = {"tiktok": "TikTok", "youtube": "YouTube", "both": "TikTok/YouTube両方"}[where]
    when = runner.slot_label(slot)
    line.reply(token, [line.text(f"📤 {name} を {dest} へ投稿中…（{when}に予約）")])
    threading.Thread(target=_job_post, args=(name, where, slot), daemon=True).start()


# --- プレビュー動画・ポスターの配信 ---------------------------------------
@app.get("/media/out/<path:fname>")
def media_out(fname: str):
    f = config.OUTPUT_DIR / fname
    if not f.exists() or ".." in fname:
        abort(404)
    return send_file(f)


@app.get("/media/cache/<path:fname>")
def media_cache(fname: str):
    f = config.MEDIA_CACHE / fname
    if not f.exists() or ".." in fname:
        abort(404)
    return send_file(f)


@app.get("/health")
def health():
    return {"ok": True, "base_url": BASE_URL}


# --- 起動 -----------------------------------------------------------------
def main() -> None:
    global BASE_URL
    lack = config.missing_secrets()
    if lack:
        raise SystemExit(f"❌ .env に {', '.join(lack)} が未設定。README参照。")

    import tunnel
    print("▶ トンネル起動中…")
    proc, url = tunnel.start(config.PORT)
    BASE_URL = url
    print(f"✔ 公開URL: {url}")

    # トンネルURLのDNS浸透に時間がかかると初回登録が弾かれるので、通るまでリトライ（自己回復）
    endpoint = f"{url}/callback"
    ok = False
    for attempt in range(1, 8):  # 最大7回・約70秒
        ok, info = line.set_webhook_endpoint(endpoint)
        if ok:
            print(f"✔ LINE webhook 自動登録: {endpoint}")
            break
        print(f"… webhook登録リトライ {attempt}/7（{info}）")
        time.sleep(10)
    if not ok:
        print(f"⚠ webhook自動登録に失敗。コンソールで {endpoint} を手動設定して")

    scheduler.start(_auto_fire)
    print(f"⏰ {scheduler.status_text().splitlines()[0]}")

    print(f"▶ サーバ待受: http://localhost:{config.PORT}")
    try:
        app.run(host="127.0.0.1", port=config.PORT)
    finally:
        proc.terminate()


if __name__ == "__main__":
    main()
