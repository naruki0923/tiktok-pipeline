"""LINE Messaging API クライアント。

- 署名検証（webhookの正当性チェック）
- reply / push でメッセージ送信（テキスト・動画・ボタン）
- webhook エンドポイントURLの自動登録（トンネルURLが変わっても貼り直し不要にする肝）
"""
from __future__ import annotations

import base64
import hashlib
import hmac

import requests

import config

_HEADERS = {
    "Authorization": f"Bearer {config.CHANNEL_ACCESS_TOKEN}",
    "Content-Type": "application/json",
}


# --- 署名検証 -------------------------------------------------------------
def verify_signature(body: bytes, signature: str) -> bool:
    """X-Line-Signature を channel secret で検証する。"""
    if not config.CHANNEL_SECRET or not signature:
        return False
    mac = hmac.new(config.CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode()
    return hmac.compare_digest(expected, signature)


# --- 送信 -----------------------------------------------------------------
def reply(reply_token: str, messages: list[dict]) -> None:
    """webhookへの即時返信（reply token は1回・短時間のみ有効）。"""
    requests.post(
        f"{config.LINE_API}/message/reply",
        headers=_HEADERS,
        json={"replyToken": reply_token, "messages": messages[:5]},
        timeout=15,
    )


def push(to: str, messages: list[dict]) -> None:
    """任意のタイミングでの送信（生成完了・投稿完了の通知に使う）。"""
    requests.post(
        f"{config.LINE_API}/message/push",
        headers=_HEADERS,
        json={"to": to, "messages": messages[:5]},
        timeout=15,
    )


# --- メッセージ部品 -------------------------------------------------------
def text(msg: str) -> dict:
    return {"type": "text", "text": msg[:5000]}


def video(url: str, preview_url: str) -> dict:
    return {"type": "video", "originalContentUrl": url, "previewImageUrl": preview_url}


def buttons(alt: str, title: str, body: str, actions: list[dict]) -> dict:
    """postback ボタン付きテンプレート。actions は最大4つ。"""
    return {
        "type": "template",
        "altText": alt,
        "template": {
            "type": "buttons",
            "title": title[:40],
            "text": body[:60],
            "actions": actions[:4],
        },
    }


def postback_action(label: str, data: str) -> dict:
    return {"type": "postback", "label": label[:20], "data": data}


# --- webhook 自動登録 ------------------------------------------------------
def set_webhook_endpoint(endpoint: str) -> tuple[bool, str]:
    """LINE側のwebhook先を endpoint に更新する。

    トンネルURLは再起動で変わり得るので、起動時に毎回これを叩けば
    人手でコンソールに貼り直す必要がなくなる。
    """
    try:
        r = requests.put(
            f"{config.LINE_API}/channel/webhook/endpoint",
            headers=_HEADERS,
            json={"endpoint": endpoint},
            timeout=15,
        )
        if r.status_code == 200:
            return True, endpoint
        return False, f"{r.status_code}: {r.text}"
    except requests.RequestException as e:
        return False, str(e)
