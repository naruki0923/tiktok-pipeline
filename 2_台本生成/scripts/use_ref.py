#!/usr/bin/env python3
"""参考動画を「採用」して、そのまま文字起こしまで流す 1_リサーチ→2_台本生成 のブリッジ。

    ./.venv/bin/python use_ref.py "https://www.tiktok.com/@user/video/1234567890"

やること:
  ① 再利用チェック … 同じ参考動画を直近30日以内に採用していたら中断（禁止ルール）。
  ② 採番        … 2_台本生成/文字起こし/ref_NNN.txt の空き連番を決める。
  ③ 文字起こし   … transcribe.py を呼んで ref_NNN.txt を生成。
  ④ 履歴記録     … 1_リサーチ/採用履歴.tsv に「採用日・動画URL・出力・メモ」を追記。
  ⑤ 次の一手     … AI台本化→format_script.py のコマンドを表示。

再利用の同一判定は動画ID（URL末尾の /video/<数字>）で行う。IDが取れないURL
（vt.tiktok.com の短縮等）はURL文字列そのままで判定する。
--force で30日ルールを無視して強行できる（履歴には[force]と残す）。
"""
import argparse
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent           # 2_台本生成/scripts
VENV_PY = HERE / ".venv" / "bin" / "python"
TRANSCRIBE = HERE / "transcribe.py"
TRANS_DIR = HERE.parent / "文字起こし"            # 2_台本生成/文字起こし
HISTORY = HERE.parent.parent / "1_リサーチ" / "採用履歴.tsv"  # 1_リサーチ/採用履歴.tsv

REUSE_BAN_DAYS = 30  # 同じ参考動画の再利用を禁止する日数


def video_key(url: str) -> str:
    """再利用判定のキー。/video/<数字> があればその数字、無ければURLそのもの。"""
    m = re.search(r"/video/(\d+)", url)
    return m.group(1) if m else url.strip().rstrip("/")


def read_history() -> list[dict]:
    if not HISTORY.exists():
        return []
    rows = []
    for line in HISTORY.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        rows.append({"date": parts[0], "url": parts[1], "out": parts[2],
                     "memo": parts[3] if len(parts) > 3 else ""})
    return rows


def check_reuse(url: str, force: bool):
    key = video_key(url)
    today = date.today()
    for r in read_history():
        if video_key(r["url"]) != key:
            continue
        try:
            used = datetime.strptime(r["date"], "%Y-%m-%d").date()
        except ValueError:
            continue
        age = (today - used).days
        if age < REUSE_BAN_DAYS:
            ok_on = used + timedelta(days=REUSE_BAN_DAYS)
            msg = (f"\n[禁止] この参考動画は {r['date']}（{age}日前）に採用済みです。"
                   f"\n  ルール: 同じ参考動画は30日間は再利用しない。"
                   f"\n  再利用OKになるのは {ok_on.isoformat()} 以降です。"
                   f"\n  → 別の参考動画を選び直してください（1_リサーチ/参考動画リスト.md）。")
            if force:
                print(msg + "\n  --force 指定のため強行します。", file=sys.stderr)
                return "[force]"
            sys.exit(msg + "\n  どうしても使う場合のみ --force。")
    return ""


def next_ref_number() -> int:
    TRANS_DIR.mkdir(parents=True, exist_ok=True)
    nums = []
    for p in TRANS_DIR.glob("ref_*.txt"):
        m = re.fullmatch(r"ref_(\d+)", p.stem)
        if m:
            nums.append(int(m.group(1)))
    return (max(nums) + 1) if nums else 1


def append_history(url: str, out: Path, memo: str):
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    if not HISTORY.exists():
        HISTORY.write_text(
            "# 採用済み参考動画の履歴（1ヶ月=30日は同じ動画の再利用禁止）\n"
            "# 採用日\t動画URL\t出力ファイル\tメモ\n",
            encoding="utf-8")
    rel = out.relative_to(HERE.parent.parent) if out.is_absolute() else out
    with HISTORY.open("a", encoding="utf-8") as f:
        f.write(f"{date.today().isoformat()}\t{url}\t{rel}\t{memo}\n")


def main():
    ap = argparse.ArgumentParser(
        description="参考動画を採用→文字起こしまで流す（30日再利用禁止チェック付き）")
    ap.add_argument("url", help="採用する参考動画のURL（TikTok等）")
    ap.add_argument("-n", "--name", default=None,
                    help="出力名を ref_<name>.txt にする（既定は ref_NNN 連番）")
    ap.add_argument("--memo", default="", help="履歴に残すメモ（任意）")
    ap.add_argument("--model", default="large-v3", help="Whisperモデル")
    ap.add_argument("--force", action="store_true",
                    help="30日再利用禁止を無視して強行")
    args = ap.parse_args()

    force_tag = check_reuse(args.url, args.force)   # ① 再利用チェック

    if args.name:
        out = TRANS_DIR / f"ref_{args.name}.txt"
    else:
        out = TRANS_DIR / f"ref_{next_ref_number():03d}.txt"  # ② 採番

    print(f"■ 採用: {args.url}\n■ 出力: {out}", file=sys.stderr)

    # ③ 文字起こし（transcribe.py に委譲）
    cmd = [str(VENV_PY), str(TRANSCRIBE), args.url, "-o", str(out),
           "--model", args.model]
    r = subprocess.run(cmd)
    if r.returncode != 0:
        sys.exit(f"\n[中断] 文字起こしに失敗しました（履歴には残しません）。")

    # ④ 履歴記録
    append_history(args.url, out, (args.memo + " " + force_tag).strip())

    # ⑤ 次の一手
    print(f"""
────────────────────────────────────────
✅ 採用＆文字起こし完了: {out}
   履歴: {HISTORY}

次にやること（2_台本生成）:
  1) {out} を AI で台本化（プロンプト_台本生成.md / 台本作成ルール.md）
  2) 出来た台本を整形して本番テキストへ:
     cd 2_台本生成/scripts
     ./.venv/bin/python format_script.py 台本.txt -o ../../3_動画生成/音声/本番_XXX.txt
────────────────────────────────────────""", file=sys.stderr)


if __name__ == "__main__":
    main()
