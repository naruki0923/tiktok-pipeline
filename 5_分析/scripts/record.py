#!/usr/bin/env python3
"""5_分析: 投稿動画の指標を1行ずつ metrics.csv に記録する。

TikTokアプリ/管理画面のインサイトを見て手動で数値を入力する運用。
確認時間は毎回そろえる（例: 投稿24時間後）と正しく比較できる。

使い方:
  # 対話モード（数値を順に聞かれる）
  ./record.py 本番_015_TikTok

  # フラグでまとめて渡す（自動化・再入力向け）
  ./record.py 本番_015_TikTok --views 3200 --avg-watch 9.5 \
      --full-view 42 --hold1s 55 --fyp 96 --likes 120 --comments 8 \
      --shares 3 --saves 15 --profile 40 --line 5 --followers 12

尺（秒）は 3_動画生成/output/<動画名>.mp4 から ffprobe で自動取得する。
取れない場合は --duration で指定するか対話で入力する。

視聴維持率(%) = 平均視聴秒 / 尺秒 * 100 は自動計算して保存する。
"""
from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]  # プロジェクトルート
METRICS = Path(__file__).resolve().parent.parent / "metrics.csv"
OUTPUT_DIR = BASE / "3_動画生成" / "output"
POST_LOG = BASE / "4_投稿" / "ログ" / "post_log.csv"

# CSVの列。順序を変えると既存データと整合しなくなるので追記のみにする。
FIELDS = [
    "動画名", "投稿日時", "計測日時", "尺秒",
    "再生数", "平均視聴秒", "視聴維持率", "フル視聴率", "継続率1秒", "おすすめ流入率",
    "いいね", "コメント", "シェア", "保存", "プロフ遷移", "LINE登録", "フォロワー増",
    "主要視聴者", "ターゲット一致", "メモ",
]

# 自チャンネルのターゲット（退職を検討している層）。視聴者属性の一致判定の基準メモ。
TARGET_PERSONA = "退職検討層（20〜40代・会社員）"


def probe_duration(video_name: str) -> float | None:
    """3_動画生成/output から尺(秒)を ffprobe で取得。"""
    stem = video_name.rsplit(".mp4", 1)[0]
    for cand in (OUTPUT_DIR / f"{stem}.mp4", OUTPUT_DIR / video_name):
        if cand.exists():
            try:
                out = subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries",
                     "format=duration", "-of", "csv=p=0", str(cand)],
                    capture_output=True, text=True, check=True,
                )
                return round(float(out.stdout.strip()), 1)
            except (subprocess.CalledProcessError, ValueError):
                return None
    return None


def lookup_post_datetime(video_name: str) -> str:
    """post_log.csv から予約/投稿日時を拾えれば投稿日時の初期値にする。"""
    stem = video_name.rsplit(".mp4", 1)[0]
    if not POST_LOG.exists():
        return ""
    hit = ""
    with POST_LOG.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            v = (row.get("video") or "").rsplit(".mp4", 1)[0]
            if v == stem:
                # url列に予約日時が入っている行を優先
                hit = row.get("url") or hit or row.get("datetime", "")
    return hit


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{prompt}{suffix}: ").strip()
    return val or default


def num(val: str) -> str:
    """空欄はそのまま空文字で保存（未計測を許容）。"""
    return val.strip()


def main() -> int:
    p = argparse.ArgumentParser(description="投稿動画の指標を metrics.csv に記録")
    p.add_argument("video", help="動画名（例: 本番_015_TikTok / .mp4は省略可）")
    p.add_argument("--duration", type=float, help="尺(秒)。省略時はffprobeで自動取得")
    p.add_argument("--posted", help="投稿日時。省略時はpost_logから推定")
    p.add_argument("--measured", help="計測日時。省略時は現在時刻")
    p.add_argument("--views"); p.add_argument("--avg-watch", dest="avg_watch")
    p.add_argument("--full-view", dest="full_view"); p.add_argument("--hold1s")
    p.add_argument("--fyp", help="おすすめ流入率(%%)")
    p.add_argument("--likes"); p.add_argument("--comments"); p.add_argument("--shares")
    p.add_argument("--saves"); p.add_argument("--profile"); p.add_argument("--line")
    p.add_argument("--followers")
    p.add_argument("--audience", help="主要視聴者(視聴者タブ)。例: 40代男性/東京")
    p.add_argument("--target-match", dest="target_match",
                   help="ターゲット一致 ○/△/✗")
    p.add_argument("--memo", default="")
    args = p.parse_args()

    video = args.video
    duration = args.duration if args.duration is not None else probe_duration(video)
    posted = args.posted if args.posted is not None else lookup_post_datetime(video)
    measured = args.measured or datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    # フラグが1つも渡っていなければ対話モード
    flag_metrics = [args.views, args.avg_watch, args.full_view, args.hold1s,
                    args.fyp, args.likes, args.comments, args.shares, args.saves,
                    args.profile, args.line, args.followers]
    interactive = all(m is None for m in flag_metrics)

    if interactive:
        print(f"■ {video} の指標を入力（分かる項目だけでOK、空欄はスキップ）")
        if duration:
            print(f"  尺: {duration}秒（自動取得）")
        else:
            duration = float(ask("尺秒（動画の長さ）", "0") or 0)
        posted = ask("投稿日時", posted)
        views = num(ask("再生数"))
        avg_watch = num(ask("平均視聴秒"))
        full_view = num(ask("フル視聴率(%)"))
        hold1s = num(ask("継続率1秒(%)  ※40%以上が目標"))
        fyp = num(ask("おすすめ流入率(%)  ※95%以上が目安"))
        likes = num(ask("いいね")); comments = num(ask("コメント"))
        shares = num(ask("シェア")); saves = num(ask("保存"))
        profile = num(ask("プロフ遷移")); line = num(ask("LINE登録"))
        followers = num(ask("フォロワー増"))
        print(f"  ▼視聴者タブ（ターゲット={TARGET_PERSONA}）")
        audience = ask("主要視聴者  例: 40代男性/東京")
        target_match = ask("ターゲット一致  ○/△/✗", "")
        memo = ask("メモ")
    else:
        views, avg_watch = num(args.views or ""), num(args.avg_watch or "")
        full_view, hold1s = num(args.full_view or ""), num(args.hold1s or "")
        fyp = num(args.fyp or "")
        likes, comments = num(args.likes or ""), num(args.comments or "")
        shares, saves = num(args.shares or ""), num(args.saves or "")
        profile, line = num(args.profile or ""), num(args.line or "")
        followers = num(args.followers or "")
        audience = args.audience or ""
        target_match = args.target_match or ""
        memo = args.memo

    # 視聴維持率 = 平均視聴秒 / 尺秒
    retention = ""
    try:
        if avg_watch and duration:
            retention = f"{float(avg_watch) / float(duration) * 100:.1f}"
    except (ValueError, ZeroDivisionError):
        retention = ""

    row = {
        "動画名": video, "投稿日時": posted, "計測日時": measured,
        "尺秒": duration or "", "再生数": views, "平均視聴秒": avg_watch,
        "視聴維持率": retention, "フル視聴率": full_view, "継続率1秒": hold1s,
        "おすすめ流入率": fyp, "いいね": likes, "コメント": comments,
        "シェア": shares, "保存": saves, "プロフ遷移": profile,
        "LINE登録": line, "フォロワー増": followers,
        "主要視聴者": audience, "ターゲット一致": target_match, "メモ": memo,
    }

    new_file = not METRICS.exists()
    with METRICS.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        w.writerow(row)

    print(f"\n✓ 記録しました → {METRICS}")
    if retention:
        print(f"  視聴維持率 {retention}%（平均視聴{avg_watch}秒 / 尺{duration}秒）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
