#!/usr/bin/env python3
"""TikTokの動画ファイルを実物のChrome経由で落とす（yt-dlpが弾かれた日の逃げ道）。

    ../../4_投稿/scripts/.venv/bin/python tiktok_dl.py <URL> -o /tmp/v.mp4

TikTokは日によって yt-dlp のリクエストにbot判定のチャレンジページを返し、
`Unexpected response from webpage request` で**全滅**する（2026-08-11に実測）。
同じ時刻に未ログインの実物Chromeでは動画ページが普通に開けるので、
ページ内の埋め込みJSONまたは video 要素から再生URLを取り、
**ブラウザのCookieを持ったまま**バイト列を取得する。

リサーチ用の未ログインChrome（.chrome-research・ポート9223）を使う。
投稿アカウントは巻き込まない（auto_research.py と同じ方針）。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "4_投稿" / "scripts"))   # browser_ctx を借りる

RESEARCH_PROFILE = HERE / ".chrome-research"
RESEARCH_PORT = 9223

# h265(bytevc1) の play_addr は404を返す動画があるので、h264 を優先して選ぶ
# （transcribe.py が -S vcodec:h264 を付けているのと同じ理由）。ただし h264 が
# 404 の場合に備えて、h265 や video 要素由来のURLも後順位の候補として残す。
_SRC_JS = r"""
(() => {
  const urls = [];
  const videoId = (location.pathname.match(/\/video\/(\d+)/) || [])[1] || null;
  const add = (url, codec = '') => {
    if (typeof url === 'string' && /^https?:\/\//.test(url)) {
      urls.push({codec: String(codec || '').toLowerCase(), url});
    }
  };
  const addVideo = video => {
    if (!video || typeof video !== 'object') return;
    for (const b of (video.bitrateInfo || [])) {
      const addr = b.PlayAddr || b.playAddr || {};
      const list = addr.UrlList || addr.urlList || [];
      for (const url of list) add(url, b.CodecType || b.codecType);
    }
    for (const key of ['playAddr', 'downloadAddr']) {
      const addr = video[key];
      if (typeof addr === 'string') add(addr, 'h264');
      else if (addr) for (const url of (addr.UrlList || addr.urlList || [])) add(url, 'h264');
    }
  };
  const walk = (value, seen = new Set()) => {
    if (!value || typeof value !== 'object' || seen.has(value)) return;
    seen.add(value);
    if (value.video && (!videoId || String(value.id || '') === videoId)) addVideo(value.video);
    for (const child of Object.values(value)) walk(child, seen);
  };

  // TikTokは配信実験によって埋め込みJSONの名前・階層が変わるため、既知の
  // scriptだけでなくJSON script全体から対象動画IDを探す。
  for (const el of document.querySelectorAll('script[type="application/json"], #SIGI_STATE')) {
    try { walk(JSON.parse(el.textContent || '')); } catch (_) {}
  }
  try { walk(window.SIGI_STATE); } catch (_) {}

  // 埋め込みJSONが省略されたクライアント描画ページの逃げ道。
  for (const el of document.querySelectorAll('video, video source')) {
    add(el.currentSrc || el.src || el.getAttribute('src'));
  }
  for (const entry of performance.getEntriesByType('resource')) {
    if (entry.initiatorType === 'video') add(entry.name);
  }

  const unique = [...new Map(urls.map(x => [x.url, x])).values()];
  const rank = x => (x.codec.includes('bytevc1') || x.codec.includes('h265')) ? 2
    : (x.codec.includes('h264') || x.codec.includes('avc')) ? 0 : 1;
  unique.sort((a, b) => rank(a) - rank(b));
  return {urls: unique.map(x => x.url), id: videoId};
})()
"""


def _has_audio_stream(path: Path) -> bool:
    """取得物がffmpegで読め、文字起こしに必要な音声を含むか確認する。"""
    try:
        p = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return p.returncode == 0 and bool(p.stdout.strip())


def download(url: str, out: Path) -> Path:
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    import browser_ctx  # noqa: PLC0415

    proc = None
    part: Path | None = None
    try:
        with sync_playwright() as p:
            _b, ctx, page, proc = browser_ctx.connect_healthy(
                p, profile_dir=RESEARCH_PROFILE, port=RESEARCH_PORT)
            # load まで待つと、動画CDNの接続が閉じないページだけ60秒で落ちる。
            # 埋め込みJSONとvideo要素はDOM構築後に読めるため、ここで十分。
            page.goto(url, timeout=60_000, wait_until="domcontentloaded")
            page.wait_for_timeout(3_500)
            src = None
            for attempt in (1, 2):
                src = page.evaluate(_SRC_JS)
                if src and src.get("urls"):
                    break
                if attempt == 1:
                    page.wait_for_timeout(4_000)
            if not src or not src.get("urls"):
                sys.exit("[中断] 動画ページから再生URLを取れませんでした"
                         "（削除/非公開、またはTikTokの確認画面の可能性）")
            last = ""
            user_agent = page.evaluate("navigator.userAgent")
            for i, media in enumerate(src["urls"], 1):
                # ブラウザのCookieとUAを引き継ぐ。CDNによってはトップページではなく
                # 元の動画ページをRefererに要求するため、実際に開いたURLを渡す。
                r = ctx.request.get(
                    media,
                    headers={"referer": page.url, "user-agent": user_agent},
                    timeout=120_000)
                if r.ok:
                    body = r.body()
                    content_type = r.headers.get("content-type", "").lower()
                    if content_type.startswith(("text/", "application/json")):
                        last = f"動画ではない応答({content_type or 'content-type不明'})"
                    elif len(body) <= 100_000:
                        last = f"本体が小さすぎる({len(body)}バイト)"
                    else:
                        out.parent.mkdir(parents=True, exist_ok=True)
                        with tempfile.NamedTemporaryFile(
                                prefix=f".{out.name}.", suffix=".part",
                                dir=out.parent, delete=False) as f:
                            f.write(body)
                            part = Path(f.name)
                        if _has_audio_stream(part):
                            os.replace(part, out)
                            part = None
                            return out
                        last = "取得物に読み取り可能な音声がない"
                        part.unlink(missing_ok=True)
                        part = None
                else:
                    last = f"HTTP {r.status}"
                print(f"  …再生URL {i}/{len(src['urls'])} が駄目（{last}）", file=sys.stderr)
            sys.exit(f"[中断] 動画本体を取得できませんでした（{last}）")
    finally:
        if part is not None:
            part.unlink(missing_ok=True)
        if proc:
            proc.terminate()


def main() -> None:
    ap = argparse.ArgumentParser(description="TikTok動画をブラウザ経由で保存する")
    ap.add_argument("url")
    ap.add_argument("-o", "--out", required=True, help="保存先(.mp4)")
    args = ap.parse_args()
    path = download(args.url, Path(args.out).resolve())
    print(path)


if __name__ == "__main__":
    main()
