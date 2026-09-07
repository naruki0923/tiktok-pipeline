#!/usr/bin/env python3
"""投稿前チェック（precheck）

投稿手段に依らない「土台」の検査。動画ファイルとキャプションを受け取り、
TikTokに上げる前に機械的に弾けるものを弾く。

チェック項目:
  - 動画ファイルが存在し、mp4であること
  - 縦型 9:16 前後であること（横長を誤って上げない）
  - 尺・ファイルサイズがTikTokの制限内であること
  - キャプション長（TikTokは概ね2200字上限。実用は短め推奨）
  - CTA（プロフィール/LINE誘導）がキャプションに入っているか（警告）
  - 誇大・断定表現の検出（景表法/コミュニティガイドライン対策）

方針(メモ [[legal-check-agent-policy]]): 基準は「断定回避」ではなく「誤認させない」。
断定表現は "禁止" ではなく "要確認" として警告に留め、事実ベースなら通す判断は人間/AIに委ねる。

単体実行:
  ./.venv/bin/python precheck.py <video.mp4> --caption "本文 #タグ"
戻り値: 問題なし=0, 警告のみ=0, 致命的エラーあり=1
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# --- TikTokの制約（2026時点のおおよその値。厳密仕様は変わりうる） ---
MAX_CAPTION_CHARS = 2200          # キャプション最大文字数
MAX_FILE_BYTES = 4 * 1024**3      # 4GB（実用上これを超えることはまず無い）
MIN_DURATION_SEC = 3             # 短すぎる動画
MAX_DURATION_SEC = 10 * 60        # 10分（アカウント権限で変動）
TARGET_ASPECT = 9 / 16           # 縦型
ASPECT_TOLERANCE = 0.05          # 9:16からの許容ズレ

# 誇大・断定表現（「必ずもらえる」等）。CLAUDE.md の注意事項に対応。
# 検出しても即NGにはせず「要確認」警告にする（事実ベースなら通す）。
HYPE_WORDS = [
    "必ず", "確実に", "絶対", "100%", "誰でももらえる", "全員", "保証",
    "簡単に稼", "楽して", "無条件", "もれなく", "今だけ", "損しない",
]


@dataclass
class CheckResult:
    errors: list[str] = field(default_factory=list)    # 致命的（投稿を止める）
    warnings: list[str] = field(default_factory=list)  # 要確認（人間判断）
    info: dict = field(default_factory=dict)           # 参考情報

    @property
    def ok(self) -> bool:
        return not self.errors


def _probe_video(path: Path) -> dict | None:
    """ffprobe で幅・高さ・尺を取得。失敗時 None。"""
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height:format=duration",
                "-of", "json", str(path),
            ],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode != 0:
            return None
        data = json.loads(out.stdout)
        stream = data.get("streams", [{}])[0]
        duration = float(data.get("format", {}).get("duration", 0) or 0)
        return {
            "width": int(stream.get("width", 0) or 0),
            "height": int(stream.get("height", 0) or 0),
            "duration": duration,
        }
    except Exception:
        return None


def check_video(path: Path, res: CheckResult) -> None:
    if not path.exists():
        res.errors.append(f"動画ファイルが見つからない: {path}")
        return
    if path.suffix.lower() != ".mp4":
        res.warnings.append(f"拡張子が.mp4でない: {path.suffix}（TikTok推奨はmp4）")

    size = path.stat().st_size
    res.info["size_mb"] = round(size / 1024**2, 1)
    if size == 0:
        res.errors.append("動画ファイルが空(0byte)")
        return
    if size > MAX_FILE_BYTES:
        res.errors.append(f"ファイルが大きすぎる: {res.info['size_mb']}MB > 4GB")

    probe = _probe_video(path)
    if probe is None:
        res.warnings.append("ffprobeで動画情報を取得できず（尺・比率チェックをスキップ）")
        return

    w, h, dur = probe["width"], probe["height"], probe["duration"]
    res.info.update(width=w, height=h, duration_sec=round(dur, 1))

    if w and h:
        aspect = w / h
        if abs(aspect - TARGET_ASPECT) > ASPECT_TOLERANCE:
            res.errors.append(
                f"縦型9:16でない: {w}x{h} (比率 {aspect:.3f}, 目標 {TARGET_ASPECT:.3f})"
            )
    if dur:
        if dur < MIN_DURATION_SEC:
            res.errors.append(f"尺が短すぎる: {dur:.1f}秒 < {MIN_DURATION_SEC}秒")
        elif dur > MAX_DURATION_SEC:
            res.warnings.append(f"尺が長い: {dur:.1f}秒 > {MAX_DURATION_SEC}秒（権限次第で不可）")


def check_caption(caption: str, res: CheckResult) -> None:
    caption = caption or ""
    n = len(caption)
    res.info["caption_chars"] = n
    if n == 0:
        res.warnings.append("キャプションが空")
    if n > MAX_CAPTION_CHARS:
        res.errors.append(f"キャプションが長すぎる: {n}字 > {MAX_CAPTION_CHARS}字")

    # ハッシュタグ数
    tags = [w for w in caption.split() if w.startswith("#")]
    res.info["hashtags"] = tags
    if not tags:
        res.warnings.append("ハッシュタグが1つも無い")

    # CTA（プロフィール/LINE誘導）— キャプション or 動画末尾の想定だが最低限文言を確認
    cta_markers = ["プロフィール", "プロフ", "LINE", "ライン", "説明会", "登録", "リンク"]
    if not any(m in caption for m in cta_markers):
        res.warnings.append("CTA文言（プロフィール/LINE誘導）がキャプションに見当たらない")

    # 誇大・断定表現
    hits = [w for w in HYPE_WORDS if w in caption]
    if hits:
        res.warnings.append(
            "断定/誇大の可能性がある表現（要確認・事実ベースならOK）: " + ", ".join(hits)
        )


def run_precheck(video: Path, caption: str) -> CheckResult:
    res = CheckResult()
    check_video(video, res)
    check_caption(caption, res)
    return res


def format_report(res: CheckResult) -> str:
    lines = []
    lines.append("── 投稿前チェック ──")
    if res.info:
        info = res.info
        parts = []
        if "width" in info:
            parts.append(f"{info['width']}x{info['height']}")
        if "duration_sec" in info:
            parts.append(f"{info['duration_sec']}秒")
        if "size_mb" in info:
            parts.append(f"{info['size_mb']}MB")
        if "caption_chars" in info:
            parts.append(f"本文{info['caption_chars']}字")
        if info.get("hashtags"):
            parts.append("タグ:" + " ".join(info["hashtags"]))
        lines.append("  " + " / ".join(parts))
    for e in res.errors:
        lines.append(f"  ❌ {e}")
    for w in res.warnings:
        lines.append(f"  ⚠️  {w}")
    if res.ok and not res.warnings:
        lines.append("  ✅ 問題なし")
    elif res.ok:
        lines.append("  ✅ 致命的エラーなし（上記の警告は要確認）")
    else:
        lines.append("  → 致命的エラーあり。投稿を中止してください。")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="TikTok投稿前チェック")
    ap.add_argument("video", type=Path, help="動画ファイル(.mp4)")
    ap.add_argument("--caption", default="", help="キャプション本文（ハッシュタグ含む）")
    args = ap.parse_args()

    res = run_precheck(args.video, args.caption)
    print(format_report(res))
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(main())
