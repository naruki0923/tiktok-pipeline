#!/usr/bin/env python3
"""YouTube Shorts 投稿（YouTube Data API v3・公式 / 規約クリーン）

3_動画生成 の完成動画（縦9:16の *_YouTube.mp4）を、運用しているYouTube
チャンネルへアップロードする。TikTok側(post_tiktok.py)と同じ思想で、投稿前チェック
(precheck) → アップロード → メタデータ設定 → 予約公開 までを1コマンドで行う。

TikTokと違い公式APIなので:
  - ブラウザ自動化・検知回避は不要（OAuthトークンで直接叩く）。
  - 予約公開は publishAt（privacyStatus=private + 指定時刻に自動公開）で確実。
  - UI変更で壊れない。無料枠で1日6本相当まで（1日1本運用には十分）。

前提:
  - 先に youtube_auth.py を実行し youtube_token.json を作成済み。
  - 動画は 3_動画生成 で作成済み（*_YouTube.mp4 / 縦9:16）。

メタデータの決め方:
  - --title 指定が無ければ、キャプション(TikTokと同じ *.txt)から自動生成
    （先頭の【…】か冒頭文＋ #Shorts、100字以内）。
  - 説明文は --description / --description-file、無ければキャプション本文を使用。
  - タグはキャプション中の #ハッシュタグから自動抽出（#は除去）。

使い方:
  # TikTokと同じキャプションファイルを流用（タイトル自動生成・翌日6:30に予約公開）
  ./.venv/bin/python youtube_upload.py ../../3_動画生成/output/本番_020_YouTube.mp4 \
      --caption-file ../投稿予定/本番_020.txt

  # タイトルを明示 / 即時公開 / 限定公開でテスト
  ./.venv/bin/python youtube_upload.py <video> --caption-file <txt> --title "退職後の国保が高すぎる人へ"
  ./.venv/bin/python youtube_upload.py <video> --caption-file <txt> --publish-now
  ./.venv/bin/python youtube_upload.py <video> --caption-file <txt> --privacy unlisted --publish-now

  # 実際には上げず precheck と認証確認だけ
  ./.venv/bin/python youtube_upload.py <video> --caption-file <txt> --dry-run
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

import precheck
import youtube_auth

SCRIPT_DIR = Path(__file__).parent
LOG_CSV = SCRIPT_DIR.parent / "ログ" / "youtube_log.csv"
JST = ZoneInfo("Asia/Tokyo")

MAX_TITLE_CHARS = 100          # YouTubeタイトル上限
MAX_DESC_CHARS = 5000          # YouTube説明文上限
DEFAULT_SCHEDULE_TIME = "06:30"  # TikTokの既定に合わせる（翌朝）
DEFAULT_CATEGORY = "22"        # People & Blogs（汎用で弾かれにくい）
SHORTS_TAG = "#Shorts"         # 説明文/タイトルに含めるとShorts判定を助ける
LINE_PREFIX = "【無料の個別相談はLINEから💬️】"  # タイトル冒頭の固定文言（2026-07-24 社長指示）


def load_caption(args) -> str:
    if args.caption_file:
        return Path(args.caption_file).read_text(encoding="utf-8").strip()
    return args.caption or ""


def extract_tags(caption: str, limit: int = 15) -> list[str]:
    """キャプション中の #ハッシュタグ を YouTubeタグ（#なし）に変換。"""
    tags = re.findall(r"#(\S+)", caption)
    # 重複除去（順序維持）
    seen, out = set(), []
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out[:limit]


def strip_hashtags(text: str) -> str:
    """本文から末尾のハッシュタグ塊を除いた「読ませる本文」を返す。"""
    # 行末や文末に続く #タグ の連なりを削る
    no_tags = re.sub(r"(\s*#\S+)+\s*$", "", text).strip()
    return no_tags or text


def title_source(caption: str) -> tuple[str, str]:
    """タイトルの材料（見出し行, ハッシュタグ列）をキャプション先頭から取り出す。

    2026-07-24以降の標準形式のキャプションは
        1行目: 【CTA】タイトル本文 #タグ…  ／ 空行 ／ 説明文本体…
    なので、タイトルに使ってよいのは **1行目だけ**。キャプション全体を渡すと
    説明文の冒頭までタイトルに巻き込む（2026-08-03 本番_034で発生。本番_033は
    タグ列が長く100字制限が偶然その手前で切れていただけ）。

    旧形式（本番_014/015）はタグが2行目にあるので、1行目にタグが無いときだけ
    最初の空行までの見出し部からタグを拾う。タイトル本文は常に1行目のみ。
    """
    lines = caption.strip().split("\n")
    head_line = lines[0].strip()
    tags = re.findall(r"#\S+", head_line)
    if not tags:
        head_block = []
        for ln in lines[1:]:
            if not ln.strip():
                break
            head_block.append(ln)
        tags = re.findall(r"#\S+", " ".join(head_block))
    return head_line, " ".join(tags)


def derive_title(caption: str, explicit: str | None) -> str:
    """タイトルを決める。明示があればそれ、無ければキャプションから生成。

    形式: 【無料の個別相談はLINEから💬️】<タイトル本文（【】なし）> <ハッシュタグ…>
    （2026-07-24 社長指示。TikTokキャプションと同じ形式をタイトル欄に丸ごと入れる／[[tiktok-caption-format]]）
    タイトル本文はキャプション1行目の【…】見出しから（無ければ1行目の冒頭を句点で区切って使う）。
    ハッシュタグは title_source() が拾った分をそのまま列挙。
    末尾に " #Shorts" を、100字に収まる範囲で付ける（Shorts判定の補助）。
    改行はタイトルに入れない（YouTube側で弾かれる／説明文の混入を防ぐ）。
    """
    if explicit:
        title = explicit.strip()
        if not title.startswith(LINE_PREFIX):
            title = f"{LINE_PREFIX}{title}"
    else:
        head_line, tag_str = title_source(caption)
        m = re.match(r"\s*【([^】]+)】", head_line)
        if head_line.startswith(LINE_PREFIX):
            # 2026-07-24以降の標準形式: キャプション自体が LINE_PREFIX で始まる。
            # このときの【…】は"CTAであってタイトルではない"ので、中身を拾うと
            # 「【CTA】CTA」と二重になり本題が消える（2026-07-30 本番_031で発生）。
            # 固定文言の後ろをタイトル本文として使う。
            body_title = strip_hashtags(head_line[len(LINE_PREFIX):]).strip()
        elif m:
            body_title = m.group(1).strip()
        else:
            body = strip_hashtags(head_line)
            # 最初の文（句点まで）か、無ければ冒頭40字
            first = re.split(r"[。！？]", body, maxsplit=1)[0].strip()
            body_title = first if first else body[:40]
        title = f"{LINE_PREFIX}{body_title}"
        if tag_str:
            title = f"{title} {tag_str}"
    # Shorts補助タグを付けられるなら付ける
    if SHORTS_TAG.lower() not in title.lower():
        if len(title) + 1 + len(SHORTS_TAG) <= MAX_TITLE_CHARS:
            title = f"{title} {SHORTS_TAG}"
    # 改行・連続空白を潰してから100字に丸める（--title 指定にも効かせる）
    title = re.sub(r"\s+", " ", title).strip()
    return title[:MAX_TITLE_CHARS].strip()


def build_description(title: str, args) -> str:
    """説明文を決める。--description(-file) 優先、無ければタイトルと同じ短い内容。

    2026-07-24 社長指示: 長い本文（キャプション全文）は説明文にも不要。
    タイトルと同じ【LINE誘導】+タイトル本文+ハッシュタグだけにする（[[youtube-title-format]]）。
    末尾に #Shorts と、あれば固定フッター(CTA/リンク)を付ける。5000字に丸める。
    """
    if args.description_file:
        desc = Path(args.description_file).read_text(encoding="utf-8").strip()
    elif args.description:
        desc = args.description.strip()
    else:
        desc = title.strip()

    parts = [desc]
    if args.footer_file and Path(args.footer_file).exists():
        parts.append(Path(args.footer_file).read_text(encoding="utf-8").strip())
    if SHORTS_TAG.lower() not in desc.lower():
        parts.append(SHORTS_TAG)
    return "\n\n".join(p for p in parts if p)[:MAX_DESC_CHARS]


def compute_publish_at(args) -> str | None:
    """予約公開のRFC3339(UTC)文字列を返す。即時公開なら None。

    既定はTikTokに合わせ「翌日の指定時刻(JST)」。JST→UTCへ変換して返す。
    """
    if args.publish_now:
        return None
    hh, mm = (int(x) for x in args.schedule_time.split(":"))
    target_date = date.today() + timedelta(days=args.schedule_days)
    dt_jst = datetime.combine(target_date, dtime(hh, mm), tzinfo=JST)
    dt_utc = dt_jst.astimezone(ZoneInfo("UTC"))
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def log_post(video: Path, status: str, video_id: str = "", url: str = "",
             title: str = "", scheduled_for: str = "") -> None:
    LOG_CSV.parent.mkdir(parents=True, exist_ok=True)
    new_file = not LOG_CSV.exists()
    with LOG_CSV.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["datetime", "video", "status", "video_id", "url",
                        "scheduled_for", "title"])
        w.writerow([
            datetime.now().isoformat(timespec="seconds"),
            video.name, status, video_id, url, scheduled_for,
            title.replace("\n", " "),
        ])


def do_upload(args) -> int:
    video = args.video.resolve()
    caption = load_caption(args)
    title = derive_title(caption, args.title)
    description = build_description(title, args)
    tags = extract_tags(caption)
    publish_at = compute_publish_at(args)

    # 予約公開は仕様上 private で登録し、publishAt 到達時に自動公開される。
    if publish_at:
        privacy = "private"
    else:
        privacy = args.privacy  # public / unlisted / private

    # 1) 投稿前チェック（TikTokと共通の土台。9:16/尺/サイズ/CTA/誇大表現）
    res = precheck.run_precheck(video, caption)
    print(precheck.format_report(res))
    if not res.ok:
        log_post(video, "precheck_failed", title=title)
        print("\n投稿前チェックで致命的エラー。中止します。")
        return 1

    print("\n── YouTube メタデータ ──")
    print(f"  タイトル: {title}")
    print(f"  タグ: {' '.join('#' + t for t in tags) if tags else '(なし)'}")
    print(f"  公開: {privacy}" + (f" / 予約公開 {publish_at} (UTC)" if publish_at else " / 即時"))
    print(f"  説明文: {len(description)}字")

    if args.dry_run:
        creds = youtube_auth.get_credentials(interactive=False)
        if not creds:
            print("\n[dry-run] トークン未取得。先に youtube_auth.py を実行してください。")
            return 1
        print("\n[dry-run] precheck OK / 認証あり。実アップロードはしません。")
        log_post(video, "dry_run", title=title)
        return 0

    # 2) 認証（無人。トークンが無ければ促して終了）
    creds = youtube_auth.get_credentials(interactive=False)
    if not creds:
        print("\n❌ 認証情報がありません。先に ./.venv/bin/python youtube_auth.py を実行してください。")
        log_post(video, "not_authenticated", title=title)
        return 1

    yt = build("youtube", "v3", credentials=creds)

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": args.category,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }
    if publish_at:
        body["status"]["publishAt"] = publish_at

    # 3) レジューム可能アップロード（大きいmp4でも分割送信・進捗表示）
    media = MediaFileUpload(str(video), chunksize=8 * 1024 * 1024, resumable=True,
                            mimetype="video/mp4")
    print(f"\nアップロード開始: {video.name} ({res.info.get('size_mb', '?')}MB)")
    try:
        request = yt.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        last_pct = -1
        while response is None:
            status, response = request.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                if pct != last_pct:
                    print(f"  … {pct}%")
                    last_pct = pct
    except HttpError as e:
        detail = getattr(e, "reason", "") or str(e)
        print(f"\n❌ アップロード失敗: {e.resp.status if e.resp else '?'} {detail}")
        # よくある原因のヒント
        if e.resp is not None and e.resp.status == 403:
            print("   403の典型: APIクォータ超過 / チャンネル未確認で予約公開不可 / スコープ不足。")
        log_post(video, "upload_failed", title=title, scheduled_for=publish_at or "")
        return 1
    except Exception as e:
        print(f"\n❌ アップロード中に予期せぬエラー: {e}")
        log_post(video, "upload_error", title=title, scheduled_for=publish_at or "")
        return 1

    vid = response["id"]
    url = f"https://youtu.be/{vid}"
    when = publish_at or "即時公開"
    print(f"\n✅ アップロード完了: {url}")
    print(f"   タイトル: {title}")
    print(f"   公開: {privacy} / {when}")
    if publish_at:
        # publishAtはUTC。人が読みやすいようJSTも表示。
        jst_str = datetime.strptime(publish_at, "%Y-%m-%dT%H:%M:%SZ") \
            .replace(tzinfo=ZoneInfo("UTC")).astimezone(JST).strftime("%Y-%m-%d %H:%M JST")
        print(f"   → {jst_str} に自動公開されます。")
    log_post(video, "uploaded", video_id=vid, url=url, title=title,
             scheduled_for=publish_at or "")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="YouTube Shorts 投稿（Data API v3）")
    ap.add_argument("video", type=Path, help="投稿する動画(.mp4 / 縦9:16)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--caption", default="", help="キャプション本文（TikTok同様 #タグ含む）")
    g.add_argument("--caption-file", help="キャプションを書いたテキストファイル")
    ap.add_argument("--title", help="動画タイトル（未指定ならキャプションから自動生成）")
    ap.add_argument("--description", help="説明文（未指定ならキャプション本文を使用）")
    ap.add_argument("--description-file", help="説明文のテキストファイル")
    ap.add_argument("--footer-file", help="説明文末尾に足す固定フッター(CTA/リンク)のファイル")
    ap.add_argument("--category", default=DEFAULT_CATEGORY,
                    help=f"カテゴリID。既定 {DEFAULT_CATEGORY}(People & Blogs)。27=教育")
    ap.add_argument("--privacy", choices=["public", "unlisted", "private"],
                    default="public", help="即時公開時の公開範囲。既定 public")
    ap.add_argument("--publish-now", action="store_true",
                    help="予約せず即時公開（既定は翌日予約公開）")
    ap.add_argument("--schedule-time", default=DEFAULT_SCHEDULE_TIME,
                    help="予約公開時刻 HH:MM(JST)。既定 06:30")
    ap.add_argument("--schedule-days", type=int, default=1,
                    help="何日後に公開するか。既定 1（翌日）")
    ap.add_argument("--dry-run", action="store_true",
                    help="precheckと認証確認のみ。実アップロードしない。")
    args = ap.parse_args()
    return do_upload(args)


if __name__ == "__main__":
    sys.exit(main())
