#!/usr/bin/env python3
"""参考動画（TikTok等のURL、またはローカルの音声/動画）を日本語で文字起こしする。

    ./.venv/bin/python transcribe.py "https://vt.tiktok.com/XXXX/" -o ../文字起こし/ref_001.txt

yt-dlp で音声を取得 → faster-whisper（既定 large-v3）で文字起こし。
出力は台本の「下書き」。Whisper は日本語の数字・同音異義語を間違えることがある
（例: 3選→3000、大損→保存）。この生テキストを ../プロンプト_台本生成.md で AI に台本化させ、
その過程で誤りを直す前提。→ 台本化後に format_script.py で整形。

前提: この scripts/.venv（yt-dlp + faster-whisper 入り）で実行する。ffmpeg 必須。
初回だけ Whisper モデルを ~/.cache に自動DL（large-v3 は約3GB）。以降は使い回す。
"""
import argparse
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
YTDLP = HERE / ".venv" / "bin" / "yt-dlp"

# ドメイン語のヒント（Whisper の initial_prompt）。専門用語の取り違えを減らす。
DEFAULT_HINT = ("退職給付金や失業保険、老齢年金、高年齢雇用継続基本給付金、"
                "失業給付金などの公的制度をわかりやすく解説する動画です。")

# TikTok側が応答を閉じない場合でも、1候補が外側の1時間上限を丸ごと
# 使い切らないよう、保険経路の各試行に上限を設ける。
YTDLP_ATTEMPT_TIMEOUT = 180


def is_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://")


def fetch_audio(url: str, workdir: str) -> Path:
    """URL から音声を mp3 で取り出す。ブラウザ経由が本線・yt-dlpは保険。"""
    mp3 = Path(workdir) / "audio.mp3"
    # 順番はブラウザ→yt-dlp。逆にしていた頃は、TikTokが yt-dlp を弾く日に
    # 待ち時間つき6回リトライで2〜3分空回りしてからブラウザに落ちていた。
    # その間にTikTok側の判定が渋くなるのか、ブラウザ経由まで巻き込まれて
    # 「削除/非公開の可能性」と誤報して全体が止まる（2026-08-16の自動実行が実例。
    # 同じURLを単体のブラウザ経由で叩くと4/4で取れた）。数値取得を
    # ブラウザ本線に寄せたのと同じ理由で、音声取得も実物のChromeを先に使う。
    mp4 = fetch_via_browser(url, workdir)
    if mp4:
        try:
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(mp4),
                            "-vn", "-acodec", "libmp3lame", str(mp3)], check=True)
        except subprocess.CalledProcessError:
            # ブラウザがチャレンジHTMLや壊れた部分データを返した場合でも、ここで
            # 全体を落とさず、下のyt-dlp保険経路を試す。
            mp3.unlink(missing_ok=True)
            print("  …ブラウザ経由の取得物から音声を読めませんでした", file=sys.stderr)
        if mp3.exists() and mp3.stat().st_size > 0:
            return mp3
    print("  …ブラウザ経由が駄目だったので yt-dlp で試します", file=sys.stderr)
    return _fetch_audio_ytdlp(url, workdir, mp3)


def _fetch_audio_ytdlp(url: str, workdir: str, mp3: Path) -> Path:
    """yt-dlp で音声を取る（ブラウザ経由が駄目だった時の保険）。"""
    out = Path(workdir) / "audio.%(ext)s"
    # UAは指定しない。以前はデスクトップUAを固定していたが、yt-dlp が TikTok の
    # JSチャレンジを自前で解くようになった今は、古いUAを被せるほど
    # "Unexpected response from webpage request" で弾かれる（2026-08 実測で
    # Chrome/124固定は1/5成功、既定UAは4/5成功）。
    #
    # -S vcodec:h264 が必須。TikTok は h265(bytevc1) を最高画質として返すが
    # その play_addr が 404 を返す動画があり、既定の画質優先だと必ず
    # "unable to download video data: HTTP Error 404" になる。h264 は生きている。
    cmd = [str(YTDLP), "-x", "--audio-format", "mp3",
           "-S", "vcodec:h264", "-o", str(out), url]
    last_err = None
    # 抽出は断続的に失敗する（"Unable to extract universal data for rehydration"）。
    # 単発の失敗はTikTok側の揺らぎなので、待ち時間を伸ばしながら粘る。
    for attempt in range(6):
        try:
            subprocess.run(cmd, check=True, timeout=YTDLP_ATTEMPT_TIMEOUT)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            last_err = e
        except FileNotFoundError:
            sys.exit(f"\n[中断] yt-dlp が見つかりません: {YTDLP}")
        if mp3.exists():
            return mp3
        if attempt < 5:
            wait = 5 * (attempt + 1)
            print(f"  …取得に失敗（{attempt + 1}/6）。{wait}秒待って再試行します",
                  file=sys.stderr)
            time.sleep(wait)
    # yt-dlp が6回とも駄目な日は、TikTokが yt-dlp のリクエストにだけ
    # botチャレンジを返している（2026-08-11 実測。全URLで
    # "Unexpected response from webpage request"）。ここまで来たらブラウザ経由も
    # 先に落ちているので、打ち手は時間を空けるか別URLに変えるかしかない。
    rc = getattr(last_err, "returncode", "?") if last_err else "?"
    sys.exit(f"\n[中断] 音声の取得に失敗しました（ブラウザ経由＋yt-dlp 6回試行・終了コード {rc}）。"
             f"\n  動画が削除/非公開になっていないか、URLが正しいか確認してください: {url}")


def fetch_via_browser(url: str, workdir: str) -> Path | None:
    """1_リサーチ/scripts/tiktok_dl.py（playwright入りvenv）で動画本体を落とす。"""
    root = HERE.parent.parent
    dl = root / "1_リサーチ" / "scripts" / "tiktok_dl.py"
    py = root / "4_投稿" / "scripts" / ".venv" / "bin" / "python"
    if not dl.exists() or not py.exists():
        return None
    out = Path(workdir) / "video.mp4"
    print("  …実物のChrome経由で動画を取得します", file=sys.stderr)
    try:
        subprocess.run([str(py), str(dl), url, "-o", str(out)],
                       cwd=str(dl.parent), check=True, timeout=600)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"  …ブラウザ経由も失敗: {e}", file=sys.stderr)
        return None
    return out if out.exists() else None


def transcribe(audio: Path, model_name: str, hint: str):
    """faster-whisper で日本語文字起こし。セグメント文字列のリストを返す。"""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit("[中断] faster-whisper が未導入です。"
                 "この scripts/.venv/bin/python で実行してください。")
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(
        str(audio), language="ja", beam_size=5,
        initial_prompt=hint or None)
    return [s.text.strip() for s in segments if s.text.strip()]


def main():
    ap = argparse.ArgumentParser(
        description="参考動画（URL/ローカル）を日本語で文字起こしする")
    ap.add_argument("source", help="TikTok等のURL、またはローカルの音声/動画ファイル")
    ap.add_argument("-o", "--out", default=None,
                    help="出力テキスト（省略時は標準出力）")
    ap.add_argument("--model", default="large-v3",
                    help="Whisperモデル（既定 large-v3。速さ優先なら medium / small）")
    ap.add_argument("--hint", default=DEFAULT_HINT,
                    help="専門用語のヒント（initial_prompt）。空文字で無効化")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        if is_url(args.source):
            print(f"■ 音声を取得: {args.source}", file=sys.stderr)
            audio = fetch_audio(args.source, tmp)
        else:
            audio = Path(args.source)
            if not audio.exists():
                sys.exit(f"[エラー] ファイルが見つかりません: {audio}")
        print(f"■ 文字起こし（{args.model}）… 初回はモデルDLで時間がかかります", file=sys.stderr)
        lines = transcribe(audio, args.model, args.hint)

    if not lines:
        sys.exit("[エラー] 文字起こし結果が空でした（音声が無い動画？）")
    text = "\n".join(lines) + "\n"

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"[出力] {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)

    chars = sum(len(ln) for ln in lines)
    print(f"\n■ 文字起こし完了: {len(lines)}行 / {chars}字（下書き。数字・用語の誤りは台本化で直す）",
          file=sys.stderr)


if __name__ == "__main__":
    main()
