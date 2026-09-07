"""トークン無しで検証できる範囲の自己テスト。"""
import base64, hashlib, hmac, json

import config
import line_client as line
import runner
import scheduler
import server

# テスト用に状態ファイルを隔離（本番のschedule.jsonを汚さない）
scheduler.STATE = config.MEDIA_CACHE / "schedule_test.json"
if scheduler.STATE.exists():
    scheduler.STATE.unlink()

# LINE送信をネットワークに出さずに記録する
_sent = []
line.reply = lambda token, msgs: _sent.append(("reply", msgs))
line.push = lambda to, msgs: _sent.append(("push", msgs))
server.line.reply = line.reply
server.line.push = line.push

# テスト用シークレットを注入
config.CHANNEL_SECRET = "testsecret"


def sign(body: bytes) -> str:
    return base64.b64encode(
        hmac.new(config.CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()
    ).decode()


# 1) 署名検証
b = b'{"events":[]}'
assert line.verify_signature(b, sign(b)) is True
assert line.verify_signature(b, "bogus") is False
print("1 署名検証 OK")

# 2) 数字→本番名 正規化
assert server._name_from("021 作って") == "本番_021"
assert server._name_from("投稿 7") == "本番_007"
assert server._name_from("なし") is None
print("2 名前正規化 OK")

# 3) webhook経由のルーティング（Flask test client + 正しい署名）
app = server.app.test_client()

def post_event(ev):
    _sent.clear()
    body = json.dumps({"events": [ev]}).encode()
    r = app.post("/callback", data=body,
                 headers={"X-Line-Signature": sign(body),
                          "Content-Type": "application/json"})
    assert r.status_code == 200, r.status_code

def reply_text():
    """直近の reply（＝webhookの即時返信）の本文。非同期pushは無視。"""
    for kind, msgs in _sent:
        if kind == "reply":
            return msgs[0].get("text", "")
    return ""

# 3a: 「作って」→ 生成開始の即時返信
post_event({"type": "message", "replyToken": "t", "source": {"userId": "u1"},
            "message": {"type": "text", "text": "999 作って"}})
assert "生成開始" in reply_text(), _sent
print("3a 生成コマンド → 即時返信 OK")

# 3b: 「投稿 999」→ 動画無いので警告
post_event({"type": "message", "replyToken": "t", "source": {"userId": "u1"},
            "message": {"type": "text", "text": "投稿 999"}})
assert "まだ無い" in reply_text(), _sent
print("3b 未生成の投稿 → 警告 OK")

# 3c: ヘルプ
post_event({"type": "message", "replyToken": "t", "source": {"userId": "u1"},
            "message": {"type": "text", "text": "ヘルプ"}})
assert "使い方" in reply_text()
print("3c ヘルプ OK")

# 3d: 不正署名は 400
body = b'{"events":[]}'
r = app.post("/callback", data=body, headers={"X-Line-Signature": "bad"})
assert r.status_code == 400
print("3d 不正署名 → 400 OK")

# 3e: postback（投稿ボタン）→ 投稿中の返信
post_event({"type": "postback", "replyToken": "t", "source": {"userId": "u1"},
            "postback": {"data": "tiktok:本番_999"}})
assert "投稿中" in reply_text(), _sent
print("3e 投稿ボタン → 投稿中返信 OK")

# 4) 既存の完成動画があればプレビュー生成（本番_021 が output にある想定）
server.BASE_URL = "https://example.trycloudflare.com"
msgs = server._preview_messages("本番_021")
kinds = [m["type"] for m in msgs]
print(f"4 プレビュー生成 OK: {kinds}")

# 5) 自動配信スケジュールのLINEコマンド
post_event({"type": "message", "replyToken": "t", "source": {"userId": "u1"},
            "message": {"type": "text", "text": "毎日20時"}})
s = scheduler.load()
assert s["enabled"] is True and s["time"] == "20:00", s
assert "オン" in reply_text()
print("5a 「毎日20時」→ オン/20:00 設定 OK")

post_event({"type": "message", "replyToken": "t", "source": {"userId": "u1"},
            "message": {"type": "text", "text": "自動 7:30"}})
assert scheduler.load()["time"] == "07:30"
print("5b 時刻変更 07:30 OK")

post_event({"type": "message", "replyToken": "t", "source": {"userId": "u1"},
            "message": {"type": "text", "text": "自動オフ"}})
assert scheduler.load()["enabled"] is False
assert "オフ" in reply_text()
print("5c 自動オフ OK")

# 6) 自動発火（生成はモックしてVOICEVOX/SSD不要に）。プレビューまで届くか
server.USER_ID = "u1"
runner.make_video = lambda name: (True, "")
scheduler.next_script = lambda: "本番_021"   # 完成済み動画で代用
_sent.clear()
server._auto_fire()
pushed = [k for k, _ in _sent]
assert pushed.count("push") >= 2, _sent   # 「作るよ」告知 + プレビュー
assert any(m.get("type") == "video" for _, msgs in _sent for m in msgs), _sent
print("6 自動発火 → 告知+プレビュー push OK")

scheduler.STATE.unlink(missing_ok=True)
print("\n✅ 全テスト通過")
