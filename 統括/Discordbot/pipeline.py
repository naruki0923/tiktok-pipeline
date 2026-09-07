"""毎日18:00に走る「リサーチ→台本→動画」の通し処理。

各工程は既存スクリプトをそのまま subprocess で呼ぶだけで、判断だけをここに集める。

    ① auto_research.py   参考動画の候補を集めて採点（黄金式・実尺・30日ルール）
    ② use_ref.py         候補を採用 → 文字起こし（whisper）→ 採用履歴に記録
    ③ write_script.py    季節性・重複を判定 → 台本 → 整形 → キャプション（LLMはここだけ）
    ④ make_video.py      TikTok/YouTube 2本を生成（--title-lines 付き）

②③は候補の上から順に試し、③で「季節性NG」「角度が重複」と判定されたら
採用履歴に【不採用】と理由を書き戻して次の候補へ進む（手運用と同じ作法）。
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
from datetime import date
from pathlib import Path
from typing import Callable

import config
import runner

HISTORY = config.ROOT / "1_リサーチ" / "採用履歴.tsv"
# Whisperまで進んだ候補の上限。音声を取得できなかった候補は重い処理に
# 入っていないため、この枠を消費させず、リサーチ済みの後続候補を試す。
MAX_SCRIPT_ATTEMPTS = 3
# TikTok側の都合で一部動画だけ音声を取れない場合にも、上の台本化試行数を
# 満たせるだけの予備を残す。auto_research は従来から採点用に最大12本を
# 実数確認していたため、その候補を6本で捨てずに受け取るだけに留める。
RESEARCH_CANDIDATE_LIMIT = 12

Log = Callable[[str], None]


class PipelineError(RuntimeError):
    """通しの途中で進めなくなった。理由をそのままDiscordに出す。"""


def _run(cmd: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess:
    """caffeinate 配下のコマンドを実行し、時間切れなら子プロセスも止める。"""
    # caffeinate で包む（無人実行中のスリープ対策。理由は runner._run のコメント）。
    # 新しいプロセスグループに隔離し、時間切れ時は caffeinate だけでなく、その下の
    # use_ref.py / yt-dlp 等もまとめて終了する。subprocess.run では直接の子しか
    # kill されず、重い孫プロセスが次候補の裏で残る可能性がある。
    full_cmd = ["/usr/bin/caffeinate", "-i", "-m", "-s",
                *(str(c) for c in cmd)]
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    try:
        process = subprocess.Popen(
            full_cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=env, start_new_session=True)
    except OSError as e:
        raise PipelineError(f"処理を起動できませんでした: {e}") from e

    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as e:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = process.communicate()
        detail = ((stderr or "") + (stdout or ""))[-700:]
        script = Path(str(cmd[1] if len(cmd) > 1 else cmd[0])).name
        raise PipelineError(
            f"{script} がタイムアウトしました（{timeout}秒）"
            + (f"\n```\n{detail}\n```" if detail else "")) from e

    return subprocess.CompletedProcess(full_cmd, process.returncode, stdout, stderr)


# --- ① リサーチ -------------------------------------------------------------
def research(log: Log) -> list[dict]:
    """候補リストを取る。playwrightが要るので 4_投稿 の venv で動かす。"""
    out = config.CACHE / f"候補_{date.today():%Y%m%d}.json"
    log("🔎 リサーチ中（TikTok検索→数値取得→黄金式で選別）…")
    p = _run([config.POST_PY, config.AUTO_RESEARCH, "-o", out,
              "--top", str(RESEARCH_CANDIDATE_LIMIT)],
             cwd=config.RESEARCH_SCRIPTS, timeout=2700)
    if not out.exists():
        raise PipelineError(f"リサーチが動きませんでした\n```\n{(p.stderr or p.stdout)[-700:]}\n```")
    data = json.loads(out.read_text(encoding="utf-8"))
    cands = data.get("candidates", [])
    if not cands:
        # どの段で落ちたか（検索が0件／実数が取れない／条件で全部落ちた）が分からないと
        # 打ち手が決まらないので、リサーチの出力の末尾をそのまま出す（2026-08-11）
        tail = "\n".join((p.stdout or "").strip().splitlines()[-20:])
        raise PipelineError(
            "参考動画の候補がゼロでした（TikTok側でブロックされているか、"
            "条件に合う動画が無い）。時間を空けて再実行するか、手動でURLを指定してください。"
            + (f"\n```\n{tail[-1200:]}\n```" if tail else ""))
    log(f"✔ 候補 {len(cands)}本。上位から試します")
    return cands


# --- ② 採用＋文字起こし -----------------------------------------------------
def adopt(url: str, log: Log) -> Path:
    """use_ref.py で採用→文字起こし。生成された ref_NNN.txt を返す。"""
    before = {p.name for p in config.TRANSCRIPT_DIR.glob("ref_*.txt")}
    log("🎧 文字起こし中（whisper large-v3・数分かかります）…")
    p = _run([config.GEN_PY, config.USE_REF, url], cwd=config.GEN_SCRIPTS, timeout=3600)
    after = {q.name for q in config.TRANSCRIPT_DIR.glob("ref_*.txt")}
    new = sorted(after - before)
    if not new:
        raise PipelineError(f"文字起こしに失敗しました\n```\n{(p.stderr or p.stdout)[-700:]}\n```")
    return config.TRANSCRIPT_DIR / new[-1]


def mark_rejected(ref_file: Path, reason: str) -> None:
    """採用履歴のメモ欄を【不採用】に書き換える。30日ロックは残す（再利用禁止は維持）。"""
    if not HISTORY.exists():
        return
    lines = HISTORY.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        cols = line.split("\t")
        if len(cols) >= 3 and cols[2].strip().endswith(ref_file.name):
            memo = cols[3] if len(cols) > 3 else ""
            cols = cols[:3] + [f"【不採用】{reason[:150]} / {memo}".strip(" /")]
            lines[i] = "\t".join(cols)
            break
    HISTORY.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- ③ 台本 -----------------------------------------------------------------
def write_script(ref_file: Path, cand: dict, log: Log) -> dict:
    """write_script.py を呼ぶ。不採用なら {"ok": False, ...} を返す（例外にしない）。"""
    meta = json.dumps({k: cand.get(k) for k in
                       ("url", "title", "views", "duration", "upload_date",
                        "author", "followers", "golden_ratio")}, ensure_ascii=False)
    log("✍️ 台本を作成中（季節性・角度の重複もここで判定）…")
    p = _run([config.GEN_PY, config.WRITE_SCRIPT, "--ref", ref_file, "--meta", meta],
             cwd=config.GEN_SCRIPTS, timeout=1800)
    tail = (p.stdout or "").strip().splitlines()
    try:
        res = json.loads(tail[-1]) if tail else {}
    except json.JSONDecodeError:
        raise PipelineError(
            f"台本生成の出力を読めませんでした\n```\n{(p.stderr or p.stdout)[-700:]}\n```") from None
    if not res:
        raise PipelineError(f"台本生成に失敗\n```\n{(p.stderr or p.stdout)[-700:]}\n```")
    return res


# --- 通し --------------------------------------------------------------------
def run_auto(log: Log) -> dict:
    """リサーチから動画生成まで。成功したら {"name":..., "title":...} を返す。"""
    skipped: list[str] = []
    script_attempts = 0
    for i, cand in enumerate(research(log), 1):
        if script_attempts >= MAX_SCRIPT_ATTEMPTS:
            break
        log(f"\n**候補{i}**: {cand.get('title', '')[:60]}\n"
            f"{cand.get('views', 0):,}再生 / {cand.get('duration', 0):.0f}秒 / {cand['url']}")
        try:
            ref = adopt(cand["url"], log)
        except PipelineError as e:
            # TikTokの抽出制限は動画単位・時間帯で揺れる。失敗した候補は
            # use_ref.py が履歴へ記録しないため、そのまま次候補を試して安全。
            skipped.append(f"候補{i}: 音声取得失敗")
            log(f"↩︎ 候補{i}の音声取得に失敗 → 次の候補へ\n{str(e).strip()}")
            continue
        script_attempts += 1
        res = write_script(ref, cand, log)
        if not res.get("ok"):
            reason = res.get("reason", "")
            skipped.append(f"候補{i}: {reason or '台本化で不採用'}")
            log(f"↩︎ 不採用: {reason} → 次の候補へ")
            mark_rejected(ref, reason)
            continue

        name = res["name"]
        runner.remember(name, title_lines=res.get("title_lines", 1),
                        angle=res.get("angle", ""), title=res.get("title", ""),
                        ref_url=cand["url"], created=date.today().isoformat())
        for w in res.get("warnings", []):
            log(f"⚠️ {w}")
        log(f"🎬 **{name}** 動画生成中（数分かかります）…")
        ok, vlog = runner.make_video(name)
        if not ok:
            raise PipelineError(f"{name} の動画生成に失敗\n```\n{vlog[-900:]}\n```")
        return {"name": name, "title": res.get("title", ""), "angle": res.get("angle", ""),
                "caption": res.get("caption", ""), "ref_url": cand["url"]}

    details = " / ".join(skipped)
    raise PipelineError(
        f"候補を{len(skipped)}本試しましたが、文字起こし失敗または台本化で"
        f"すべて見送りになりました。{(details + '。 ') if details else ''}"
        "検索語を変えるか、手動で参考動画URLを指定してください。")


def run_revise(name: str, instruction: str, log: Log) -> dict:
    """既存の台本に手直しを入れて動画を作り直す（Discordの「直して」）。

    リサーチも季節性判定もしないので、LLM呼び出しは1回だけ。
    """
    if not runner.script_txt(name).exists():
        raise PipelineError(f"{name} の台本がありません")
    log(f"✏️ **{name}** を直します: 「{instruction}」")
    p = _run([config.GEN_PY, config.REVISE_SCRIPT, "--name", name,
              "--instruction", instruction], cwd=config.GEN_SCRIPTS, timeout=1800)
    tail = (p.stdout or "").strip().splitlines()
    try:
        res = json.loads(tail[-1]) if tail else {}
    except json.JSONDecodeError:
        raise PipelineError(
            f"手直しの結果を読めませんでした\n```\n{(p.stderr or p.stdout)[-700:]}\n```") from None
    if not res.get("ok"):
        raise PipelineError(f"手直しできませんでした: {res.get('reason', '理由不明')}")

    runner.remember(name, title_lines=res.get("title_lines", 1), title=res.get("title", ""))
    if res.get("note"):
        log(f"📝 {res['note']}")
    for w in res.get("warnings", []):
        log(f"⚠️ {w}")
    log(f"🎬 **{name}** 動画を作り直しています…")
    ok, vlog = runner.make_video(name)
    if not ok:
        raise PipelineError(f"{name} の作り直しに失敗\n```\n{vlog[-900:]}\n```")
    return {"name": name, "title": res.get("title", ""), "angle": res.get("note", "")}


def run_from_url(url: str, log: Log) -> dict:
    """参考動画URLを人が指定したときの短縮版（リサーチ工程を飛ばす）。"""
    if not re.search(r"tiktok\.com", url):
        raise PipelineError(f"TikTokのURLに見えません: {url}")
    ref = adopt(url, log)
    res = write_script(ref, {"url": url}, log)
    if not res.get("ok"):
        mark_rejected(ref, res.get("reason", ""))
        raise PipelineError(f"不採用: {res.get('reason', '')}")
    name = res["name"]
    runner.remember(name, title_lines=res.get("title_lines", 1), angle=res.get("angle", ""),
                    title=res.get("title", ""), ref_url=url, created=date.today().isoformat())
    log(f"🎬 **{name}** 動画生成中…")
    ok, vlog = runner.make_video(name)
    if not ok:
        raise PipelineError(f"{name} の動画生成に失敗\n```\n{vlog[-900:]}\n```")
    return {"name": name, "title": res.get("title", ""), "angle": res.get("angle", ""),
            "caption": res.get("caption", ""), "ref_url": url}
