#!/usr/bin/env python3
"""TikTok投稿（Playwright / ブラウザ自動化）

3_動画生成 の完成動画を TikTok にアップロードする。

自動化する一連の流れ（2026-07-15 に実地確認）:
  アップロード → キャプション → BGM追加（お気に入りの楽曲）→ BGM音量 -20dB
  → 編集を保存 → 翌日6:30に予約 → （半自動なら確定ボタンだけ人間 / --autoで自動）

設計方針（[[project-vision-sns-agency]] / [[posting-defaults]] / CLAUDE.md「まず最小構成で通す」）:
  - 既定は「半自動」= 予約設定まで自動、最後の「投稿予約する」確定ボタンは
    人間が押す（規約・BANリスクを最小化。1日1本の運用ペース想定）。
  - アカウントが育ったら --auto を付けて確定ボタンまで自動化（無人運用へ）。
  - 認証はパスワードを扱わず、login.py で保存した専用Chromeプロファイルを再利用。

前提:
  - 先に login.py でログイン&セッション保存済み（.chrome-profile/ 存在）
  - VOICEVOX/SSD等は不要（動画は 3_動画生成 で作成済みの前提）

使い方:
  # 半自動（既定・推奨）: BGM・音量・翌日6:30予約まで自動。最後の確定だけ自分で押す
  ./.venv/bin/python post_tiktok.py ../../3_動画生成/output/本番_015_TikTok.mp4 \
      --caption-file ../投稿予定/本番_015.txt

  # 完全自動（確定ボタンまで押す。運用が安定してから）
  ./.venv/bin/python post_tiktok.py <video> --caption-file <txt> --auto

  # BGMなし / 即時投稿 / 時刻や曲を変える
  ./.venv/bin/python post_tiktok.py <video> --caption-file <txt> --no-bgm --post-now
  ./.venv/bin/python post_tiktok.py <video> --caption-file <txt> --schedule-time 07:00 --bgm-volume -18

  # 動作確認だけ（アップロードせず precheck とセッション確認のみ）
  ./.venv/bin/python post_tiktok.py <video> --caption-file <txt> --dry-run
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from playwright.sync_api import (
    Error as PWError,
    Page,
    TimeoutError as PWTimeout,
    sync_playwright,
)

import browser_ctx
import editor_ops
import precheck

SCRIPT_DIR = Path(__file__).parent
LOG_CSV = SCRIPT_DIR.parent / "ログ" / "post_log.csv"
UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload"
CONTENT_URL = "https://www.tiktok.com/tiktokstudio/content"

# TikTokのUIは頻繁に変わるため、セレクタは複数フォールバックで試す。
CAPTION_SELECTORS = [
    "div[contenteditable='true'][role='combobox']",
    "div.public-DraftEditor-content",
    "div[contenteditable='true']",
    "div[data-e2e='upload-caption'] div[contenteditable='true']",
]
POST_BUTTON_SELECTORS = [
    "button[data-e2e='post_video_button']",
    "button:has-text('投稿予約する')",
    "button:has-text('投稿')",
    "button:has-text('Schedule')",
    "button:has-text('Post')",
]
CONFIRMED_STATUSES = {
    "予約_auto", "投稿_auto", "予約_semi_auto", "投稿_semi_auto",
}


def load_caption(args) -> str:
    if args.caption_file:
        return Path(args.caption_file).read_text(encoding="utf-8").strip()
    return args.caption or ""


def log_post(video: Path, caption: str, status: str, url: str = "") -> None:
    LOG_CSV.parent.mkdir(parents=True, exist_ok=True)
    new_file = not LOG_CSV.exists()
    with LOG_CSV.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["datetime", "video", "status", "url", "caption"])
        w.writerow([
            datetime.now().isoformat(timespec="seconds"),
            video.name, status, url, caption.replace("\n", " "),
        ])


def confirmed_post_datetime(video: Path) -> str | None:
    """同じ動画の確定済み記録があれば日時を返す（重複投稿防止）。"""
    if not LOG_CSV.exists():
        return None
    try:
        with LOG_CSV.open(newline="", encoding="utf-8") as f:
            for row in reversed(list(csv.DictReader(f))):
                if (row.get("video") == video.name
                        and row.get("status") in CONFIRMED_STATUSES):
                    return row.get("datetime") or "日時不明"
    except (OSError, csv.Error):
        # ログが一時的に読めないだけで正規の投稿を妨げない。
        return None
    return None


def set_file_via_cdp(context, page, selector: str, filepath: str) -> bool:
    """CDPのDOM.setFileInputFilesでローカルファイルを直接指定する。

    connect_over_cdp接続だと set_input_files は50MB超を拒否する（別マシン想定）。
    DOM.setFileInputFiles はブラウザ自身がローカルパスを読むのでサイズ制限がない。
    メインフレーム＋全iframe＋Shadow DOMを走査し、動画用の file input に設定する。

    アップロード画面には動画用のほかにフィードバック用（accept=application/pdf）の
    file input も居る。素朴に「最初に見つかったもの」を使うとPDF欄にmp4を入れて
    しまい、画面上は何も起きないまま無言で失敗する（2026-08-25 本番_055）。
    accept を見て動画用を選ぶ。
    """
    cdp = context.new_cdp_session(page)
    try:
        doc = cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})
        # pierce=TrueでもShadow DOMは children ではなく shadowRoots に入る。
        # TikTok StudioのアップロードUIはShadow DOM化されることがあるため、
        # 通常ノード・iframe・Shadow DOMをすべて辿ってfile inputを探す。
        candidates: list[tuple[int, str]] = []   # (nodeId, accept)
        stack = [doc["root"]]
        while stack:
            node = stack.pop()
            attrs = node.get("attributes", [])
            attr_map = {
                str(attrs[i]).lower(): str(attrs[i + 1]).lower()
                for i in range(0, len(attrs) - 1, 2)
            }
            if (node.get("nodeName", "").upper() == "INPUT"
                    and attr_map.get("type") == "file"):
                candidates.append((node["nodeId"], attr_map.get("accept", "")))
            if node.get("contentDocument"):
                stack.append(node["contentDocument"])
            for shadow_root in node.get("shadowRoots", []) or []:
                stack.append(shadow_root)
            # popはLIFOなので、逆順に積んで文書順に辿る。
            for child in reversed(node.get("children", []) or []):
                stack.append(child)
        node_id = _pick_video_file_input(candidates)
        if node_id is None:
            return False
        cdp.send("DOM.setFileInputFiles", {"files": [filepath], "nodeId": node_id})
        return True
    finally:
        cdp.detach()


def _pick_video_file_input(candidates: list[tuple[int, str]]) -> int | None:
    """file input の候補から動画用を選ぶ。(nodeId, accept) の並びは文書順。"""
    for node_id, accept in candidates:
        if "video" in accept:
            return node_id
    for node_id, accept in candidates:
        if not accept:            # accept未指定なら何でも受ける欄＝動画も可
            return node_id
    return None


def _find_first(page: Page, selectors: list[str], timeout: int = 15000):
    """複数セレクタを順に試し、最初に見つかった要素を返す。"""
    last_err = None
    for sel in selectors:
        try:
            el = page.wait_for_selector(sel, timeout=timeout, state="visible")
            if el:
                return el
        except PWTimeout as e:
            last_err = e
            continue
    if last_err:
        raise last_err
    return None


def _normalize_caption(text: str) -> str:
    """比較用に空白類を正規化（改行・連続スペースの表示差異を無視するため）。"""
    return " ".join(text.split())


RESUME_EDIT_MODAL_TEXT = "編集を続けますか"


def dismiss_resume_edit_modal(page: Page) -> bool:
    """「編集していた動画は保存されませんでした。編集を続けますか？」を閉じる。

    前回の投稿が確定せずに終わるとこのモーダルが残り、アップロード欄が触れない
    まま無言で止まる（2026-08-25 本番_055）。動画は入れ直すので「破棄する」を選ぶ。
    """
    try:
        if RESUME_EDIT_MODAL_TEXT not in page.inner_text("body"):
            return False
    except (PWError, PWTimeout):
        return False
    for sel in ("button:has-text('破棄する')", "text=破棄する", "button:has-text('破棄')"):
        try:
            page.locator(sel).first.click(timeout=3000)
            page.wait_for_timeout(1500)
            print("  前回の未保存編集のダイアログを『破棄する』で閉じました。")
            return True
        except (PWError, PWTimeout):
            continue
    print("⚠️ 『編集を続けますか？』のダイアログを閉じられませんでした。手動で閉じてください。")
    return False


def _caption_key(caption: str) -> str:
    """コンテンツ一覧と突き合わせるための鍵。

    キャプションの先頭【…】は全動画共通の固定文言、末尾の#タグも被りやすいので、
    その動画だけが持つ本文部分（タイトル）を取り出す。
    """
    body = _normalize_caption(caption)
    if "】" in body:
        body = body.split("】", 1)[1]
    return body.split("#", 1)[0].strip()


def post_exists_in_studio(context, caption: str) -> bool | None:
    """TikTok Studio のコンテンツ一覧に、そのキャプションの投稿（予約分を含む）があるか。

    True=見つかった / False=一覧は出たが無い / None=確認できなかった。
    NoneとFalseはどちらも「確定した」とは扱わない。
    """
    key = _caption_key(caption)
    if not key:
        return None
    page = None
    try:
        page = context.new_page()
        page.goto(CONTENT_URL, wait_until="domcontentloaded")
        for _ in range(10):
            page.wait_for_timeout(2000)
            text = _normalize_caption(page.inner_text("body"))
            if key in text:
                return True
            if "件の投稿" in text or "posts" in text.lower():
                return False   # 一覧は描画済み。載っていない＝未確定。
        return None
    except (PWError, PWTimeout):
        return None
    finally:
        if page is not None:
            try:
                page.close()
            except PWError:
                pass


def _target_was_closed(page: Page, error: PWError) -> bool:
    """Playwrightの版に依存せず、ページ／ブラウザ終了エラーだけを判定する。"""
    return page.is_closed() or "Target page, context or browser has been closed" in str(error)


def set_caption(page: Page, caption: str) -> None:
    """DraftJSエディタに既存文言（ファイル名）が入っている。全消去→行ごとに入力。

    注意: 1行を一気に type すると先頭行が落ちることがあるため、focus→フォーカス確定→
    Meta+A→Delete で確実に消してから、改行はEnter・語はtypeで入れる。
    ハッシュタグ候補ポップアップは都度Escで閉じる。

    TikTokの一時オーバーレイ（#__ABoverlay）が表示されている間も、クリックではなく
    DOMフォーカスならポインターを奪われず、安全に対象欄だけを選択できる。
    """
    el = _find_first(page, CAPTION_SELECTORS, timeout=30000)
    el.scroll_into_view_if_needed()
    el.focus()
    page.wait_for_timeout(400)
    page.keyboard.press("Meta+A")
    page.keyboard.press("Delete")
    page.wait_for_timeout(300)
    for li, line in enumerate(caption.split("\n")):
        if li > 0:
            page.keyboard.press("Enter")
            page.wait_for_timeout(150)
        for token in line.split(" "):
            if not token:
                continue
            page.keyboard.type(token, delay=25)
            page.keyboard.type(" ")
            if token.startswith("#"):
                page.wait_for_timeout(600)
                page.keyboard.press("Escape")
    page.wait_for_timeout(600)


def read_caption_text(page: Page) -> str:
    """現在キャプション欄に入っている実際のテキストを読み戻す（検証用）。"""
    el = _find_first(page, CAPTION_SELECTORS, timeout=5000)
    return el.inner_text()


def set_caption_verified(page: Page, caption: str, retries: int = 2) -> tuple[bool, str]:
    """set_caption実行後に実際の入力内容を読み戻し、指定文と一致するか検証。
    不一致ならクリアして再入力（最大 retries 回）。

    背景: TikTokのハッシュタグ候補ポップアップ等の干渉で、typeした内容と
    実際にエディタへ反映される内容がズレることがある（無言で崩れるため要検証）。
    戻り値: (一致したか, 最後に読み取った実際のテキスト)
    """
    target = _normalize_caption(caption)
    actual = ""
    for attempt in range(retries + 1):
        set_caption(page, caption)
        page.wait_for_timeout(300)
        try:
            actual = read_caption_text(page)
        except PWTimeout:
            actual = ""
        if _normalize_caption(actual) == target:
            return True, actual
        if attempt < retries:
            print(f"  ⚠️ キャプション内容が指定と不一致（{attempt + 1}回目）。再入力します。")
    return False, actual


def do_post(args) -> int:
    video = args.video.resolve()
    caption = load_caption(args)

    confirmed_at = confirmed_post_datetime(video)
    if confirmed_at and not args.force_repost:
        print(f"✅ 過去の投稿確定を検知しました: {video.name}（{confirmed_at}）")
        print("   重複投稿を防ぐため、TikTokのブラウザ操作は行いません。")
        print("   意図的に再投稿する場合だけ --force-repost を付けてください。")
        return 0

    # 1) 投稿前チェック
    res = precheck.run_precheck(video, caption)
    print(precheck.format_report(res))
    if not res.ok:
        log_post(video, caption, "precheck_failed")
        print("\n投稿前チェックで致命的エラー。中止します。")
        return 1
    if res.warnings and not args.yes and not args.auto and sys.stdin.isatty():
        print("\n⚠️ 警告があります。続行して投稿画面を開きますか？ [y/N]: ", end="")
        if input().strip().lower() not in ("y", "yes"):
            log_post(video, caption, "aborted_by_user")
            return 1

    if args.dry_run:
        if not browser_ctx.has_session():
            print("\n[dry-run] ログインプロファイルが無い。先に login.py を実行してください。")
            return 1
        print("\n[dry-run] precheck OK / セッションあり。実投稿はしません。")
        log_post(video, caption, "dry_run")
        return 0

    if not browser_ctx.has_session():
        print(f"\n❌ セッション未保存: {browser_ctx.PROFILE_DIR}\n   先に ./.venv/bin/python login.py を実行してください。")
        return 1

    # 2) ブラウザ起動（自動化フラグなし）→ 接続 → アップロード
    # connect_healthy: 開きっぱなしのChromeが応答しない時は落として起動し直す
    with sync_playwright() as p:
        # 既存タブは前回の確認画面や分析処理が使っている可能性がある。
        # 投稿専用の新規タブを作り、別操作によるタブ終了の巻き添えを避ける。
        browser, context, page, _proc = browser_ctx.connect_healthy(
            p, new_page=True
        )
        # 前回の未保存編集が残っていても遷移をブロックしないよう、確認ダイアログは承認
        page.on("dialog", lambda d: d.accept())
        print(f"\nアップロードページを開いています: {UPLOAD_URL}")
        page.goto(UPLOAD_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        dismiss_resume_edit_modal(page)

        if "login" in page.url:
            print("❌ ログイン画面に飛ばされました。セッション切れです。login.py を再実行してください。")
            log_post(video, caption, "session_expired")
            return 1

        # ファイル入力欄が現れるまで待ち、CDP経由でローカル動画を直接指定
        try:
            page.wait_for_selector("input[type='file']", timeout=20000, state="attached")
        except PWTimeout:
            print("❌ ファイル入力欄が見つかりませんでした（UI変更の可能性）。")
            log_post(video, caption, "upload_input_not_found")
            print("   Chromeは開いたままにします。手動でアップロード/投稿できます。")
            return 1
        if set_file_via_cdp(context, page, "input[type='file']", str(video)):
            print(f"動画をアップロード中: {video.name}")
        else:
            print("❌ file input への設定に失敗（CDP）。手動でアップロードしてください。")
            log_post(video, caption, "upload_set_failed")
            return 1

        # アップロード完了（キャプション欄が出る）まで待つ。処理の重さで遅れることがあるので再試行。
        caption_ok = False
        actual_caption = ""
        for attempt in range(2):
            try:
                caption_ok, actual_caption = set_caption_verified(page, caption)
                if caption_ok:
                    print("✅ キャプションを入力しました（内容確認OK）。")
                else:
                    print("⚠️ 再入力しても内容が指定と一致しませんでした。手動で確認・修正してください。")
                    print(f"   指定: {caption[:120]}{'…' if len(caption) > 120 else ''}")
                    print(f"   実際: {actual_caption[:120]}{'…' if len(actual_caption) > 120 else ''}")
                break
            except PWTimeout:
                page.wait_for_timeout(4000)
            except PWError as e:
                if not _target_was_closed(page, e):
                    raise
                print("❌ 投稿用タブまたはChromeが処理中に閉じられました。投稿は確定していません。")
                print("   TikTokだけ投稿準備をやり直してください。")
                log_post(video, caption, "browser_closed")
                return 1
        if not caption_ok and not actual_caption:
            print("⚠️ キャプション欄が見つかりませんでした。手動で入力してください。")

        # BGM（TikTok楽曲）を追加し音量を下げる → 編集を保存
        # 注意: add_bgm は編集画面を開く。成否に関わらず最後に save_editor で
        # 必ず編集画面を閉じてから予約へ進む（開いたままだと予約UIに触れない）。
        if not args.no_bgm:
            print(f"\nBGMを追加します: {args.bgm_name}（音量 {args.bgm_volume}dB）")
            if editor_ops.add_bgm(page, args.bgm_name):
                editor_ops.set_bgm_volume(page, args.bgm_volume)
            else:
                print("⚠️ BGM追加を確認できず。Chromeで手動確認してください（他の設定は続行）。")
                # 画面のログは投稿が終われば消える。BGM無しで公開されたことに後から
                # 気づけるよう、schedule_failed と同じくCSVにも残す（2026-09-05）。
                log_post(video, caption, "bgm_failed")
            editor_ops.save_editor(page)  # 編集画面を必ず閉じる

        # 投稿予約（既定: 翌日の指定時刻）
        scheduled_for = ""
        if not args.post_now:
            target_date = (date.today() + timedelta(days=args.schedule_days)).isoformat()
            print(f"\n投稿予約を設定します: {target_date} {args.schedule_time}")
            if editor_ops.set_schedule(page, target_date, args.schedule_time):
                scheduled_for = f"{target_date} {args.schedule_time}"
            else:
                print("⚠️ 予約設定に不備。画面で日時を確認してください。")
                log_post(video, caption, "schedule_failed")

        action = "予約" if not args.post_now else "投稿"
        finished = False   # 確定まで終わったか（＝Chromeを閉じてよいか）
        if args.auto and not args.post_now and not scheduled_for:
            # 予約日時が入らないまま確定を押すと「今日の過去時刻」で投げることになる。
            # 無人で壊れた予約を作らないよう、ここで人間に渡す（2026-08-25）。
            print("❌ 予約日時が設定できていないので、完全自動でも確定ボタンは押しません。")
            print("   Chromeの画面で日付を直してから【予約する】を押してください。")
            log_post(video, caption, "auto_aborted_no_schedule")
        elif args.auto:
            # 完全自動：確定ボタン（投稿予約する / 投稿）を押す
            print(f"完全自動モード: 【{action}】ボタンを押します…")
            try:
                btn = _find_first(page, POST_BUTTON_SELECTORS, timeout=30000)
                # アップロード処理中はボタンが disabled。有効化を待つ。
                for _ in range(60):
                    if btn.is_enabled():
                        break
                    page.wait_for_timeout(1000)
                btn.click()
                page.wait_for_timeout(5000)
                if post_exists_in_studio(context, caption):
                    print(f"✅ {action}を確定しました（コンテンツ一覧で確認済み）。")
                    log_post(video, caption, f"{action}_auto", scheduled_for or page.url)
                    finished = True
                else:
                    print(f"⚠️ 【{action}】は押しましたが、コンテンツ一覧に見つかりません。")
                    print("   確定できていない可能性があるので投稿済みとしては記録しません。")
                    log_post(video, caption, "confirm_unverified", scheduled_for or page.url)
            except PWTimeout:
                print(f"❌ 【{action}】ボタンが見つからず/有効化されず。Chromeは開いたまま。手動で確定してください。")
                log_post(video, caption, "post_button_failed", scheduled_for)
        else:
            # 半自動（既定）：ここで人間にバトンタッチ。
            # バックグラウンド実行でも動くよう input() ではなくポーリングで待つ。
            print()
            print("=" * 60)
            print(f"  半自動モード: 動画・キャプション・BGM・予約設定は完了。")
            if scheduled_for:
                print(f"  予約: {scheduled_for}")
            print("  Chrome画面で最終確認し、問題なければ自分で")
            print(f"  【{action}する】ボタンを押してください。")
            print("=" * 60)
            log_post(video, caption, "ready_semi_auto", scheduled_for)
            # 確定するとアップロードページから離脱する。最大10分監視。
            posted = False
            for _ in range(200):  # 200 * 3s = 10分
                try:
                    if "upload" not in page.url:
                        posted = True
                        break
                except Exception:
                    break  # ユーザーがタブ/窓を閉じた
                time.sleep(3)
            if posted:
                # URLが upload から離れただけでは確定の証拠にならない。
                # 同じChromeで別サイトを開かれただけでも離脱するため
                # （2026-08-25 本番_055 で未投稿を「✅予約完了」と誤報告した）、
                # コンテンツ一覧に実際に載ったかどうかで判定する。
                if post_exists_in_studio(context, caption):
                    print(f"✅ {action}の確定を確認しました（コンテンツ一覧に反映）。")
                    log_post(video, caption, f"{action}_semi_auto", scheduled_for or page.url)
                    finished = True
                else:
                    print("⚠️ アップロード画面から離れましたが、コンテンツ一覧に見つかりません。")
                    print("   確定できていない可能性が高いので投稿済みとしては記録しません。")
                    log_post(video, caption, "confirm_unverified", scheduled_for or page.url)

        # browser.close() は呼ばない。connect_over_cdp の Browser.close は
        # 接続解除ではなく共有中の実Chromeを閉じ、別処理を TargetClosedError にする。
        # with sync_playwright() の終了でCDP接続だけが破棄される。

    # 用が済んだらChrome自体も閉じる。
    # 開きっぱなしにするとmacOSがDockのChromeクリックをこの投稿用プロファイルに
    # 向けてしまい、普段使いのChromeが開けなくなるため（2026-08-09 社長指摘）。
    # ただし人手の作業が残っている時（確定を押していない/失敗）は閉じない。
    if args.keep_chrome:
        print("\n（--keep-chrome: Chromeは開いたままにします）")
    elif finished:
        print("\n投稿用Chromeを閉じます。")
        browser_ctx.kill_chrome()
    else:
        print("\n未確定のためChromeは開いたままにします。確定後は手動で閉じてください。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="TikTok投稿（Playwright）")
    ap.add_argument("video", type=Path, help="投稿する動画(.mp4)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--caption", default="", help="キャプション本文（ハッシュタグ含む）")
    g.add_argument("--caption-file", help="キャプションを書いたテキストファイル")
    ap.add_argument("--auto", action="store_true",
                    help="完全自動（確定ボタンまで押す）。既定は半自動。")
    ap.add_argument("--dry-run", action="store_true",
                    help="precheckとセッション確認のみ。ブラウザ操作しない。")
    ap.add_argument("--yes", "-y", action="store_true",
                    help="警告があっても確認プロンプトを出さず進める。")
    ap.add_argument("--keep-chrome", action="store_true",
                    help="確定後もChromeを閉じない。既定は閉じる"
                         "（普段使いのChromeがDockから開けなくなるため）。")
    ap.add_argument("--force-repost", action="store_true",
                    help="確定済みログがある同じ動画を意図的に再投稿する。")
    # BGM（TikTok楽曲）
    ap.add_argument("--no-bgm", action="store_true", help="BGM追加をスキップ")
    ap.add_argument("--bgm-name", default=editor_ops.DEFAULT_BGM_NAME,
                    help="お気に入りから追加する楽曲名（前方一致）")
    ap.add_argument("--bgm-volume", type=int, default=editor_ops.DEFAULT_BGM_VOLUME_DB,
                    help="BGM音量(dB、範囲-60〜20)。既定 -20")
    # 予約投稿
    ap.add_argument("--post-now", action="store_true",
                    help="予約せず即時投稿（既定は翌日予約）")
    ap.add_argument("--schedule-time", default=editor_ops.DEFAULT_SCHEDULE_TIME,
                    help="予約時刻 HH:MM。既定 06:30")
    ap.add_argument("--schedule-days", type=int, default=1,
                    help="何日後に予約するか。既定 1（翌日）")
    args = ap.parse_args()
    return do_post(args)


if __name__ == "__main__":
    sys.exit(main())
