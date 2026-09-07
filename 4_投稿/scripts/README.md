# 4_投稿 scripts — 自動投稿（TikTok / YouTube）

3_動画生成 の完成動画を各SNSへ投稿する。**投稿先は2つ**:
- **TikTok**（`post_tiktok.py`）… ブラウザ自動化(Playwright/CDP)。`*_TikTok.mp4` を使う。
- **YouTube Shorts**（`youtube_upload.py`）… 公式 YouTube Data API v3。`*_YouTube.mp4` を使う。

投稿前チェック `precheck.py` は投稿手段に依らない共通の土台で、両方から使う。

---

## TikTok（Playwright / CDP）

3_動画生成 の完成動画を TikTok へ投稿する。1コマンドで
**アップロード → キャプション → BGM追加 → 音量-20dB → 翌日6:30に予約** まで自動。
認証はパスワードを扱わず、一度手動ログイン → 専用Chromeプロファイルを再利用する方式。

## 方式と規約について（重要）
- 手段は **ブラウザ自動化**。厳密にはTikTok規約上グレー〜違反寄り。
- 現実的リスクは法的措置ではなく **アカウントBAN/シャドウバン**。検知されやすいのは
  「大量投稿・複数アカウント・機械的な等間隔・スクレイピング」。
- 本運用は **1日1本**（人間の運用ペース）なので機械的検知リスクは低め。ただし保証はない。
- 既定は **半自動**（BGM・予約設定まで自動、最後の「投稿予約する」確定ボタンは人間）。
  アカウントが育ったら `--auto` で無人化する。
- 完全にクリーンな道は公式 Content Posting API（要審査）。将来の乗り換え先。

## 検知回避の要点（なぜCDP接続か）
TikTokは **Playwrightが起動したChrome** をログイン時に自動化ツールと検知しブロックする
（`--enable-automation` 等で `navigator.webdriver` が立つため）。実際、Playwright起動だと
ログイン即「試行回数上限」になる。そこで `browser_ctx.py` は:
- Chromeを **自動化フラグなしでsubprocess起動**（＝手で開いた普通のChromeと同じ）
- Playwrightは起動に関与せず **`connect_over_cdp` で後から接続**（webdriverは立たない）
- セッションは専用プロファイル `.chrome-profile/` に保存・再利用
- 動画アップロードは50MB制限を避け **CDPの `DOM.setFileInputFiles`** でローカル指定

### 投稿用Chromeは終わったら閉じる（既定）
投稿用Chromeを開きっぱなしにすると、macOSがDockの
Chromeクリックを**この投稿用プロファイルに向けてしまい、普段使いのChromeが開けなくなる**
（同じ `Google Chrome.app` を共有していてDockアイコンが1つしかないため）。
そこで `post_tiktok.py` は**確定まで終わったらChromeを閉じる**。
ログイン情報は `.chrome-profile/` にディスク保存されるので閉じても消えない。
- 確定していない時（半自動で人が押さなかった／確定ボタン失敗）は**開いたまま**残す
- 常に開いたままにしたい時は `--keep-chrome`

## セットアップ（初回のみ）
```bash
cd 4_投稿/scripts
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m playwright install chromium   # chromium実体（CDP接続には実Chromeを使用）
```
※ 実Chrome（/Applications/Google Chrome.app）が必要。

## 使い方

### 1. ログイン（初回 & セッション切れ時）
普通のChromeが開くので **画面で手動ログイン**（投稿用アカウント）。
ログイン完了を自動検知してプロファイルに保存する（ターミナル操作不要）。
```bash
./.venv/bin/python login.py
```

### 2. 投稿前チェック（単体でも使える）
```bash
./.venv/bin/python precheck.py ../../3_動画生成/output/本番_015_TikTok.mp4 \
    --caption "$(cat ../投稿予定/本番_015.txt)"
```
縦型9:16 / 尺 / サイズ / キャプション長 / CTA有無 / 誇大表現 を検査。

### 3. 投稿（BGM・予約込み）
```bash
# 半自動（既定・推奨）: BGM「Ghibli-like piano solo ballad」を音量-20dBで足し、
# 翌日6:30に予約設定まで自動。最後の【投稿予約する】だけ自分で押す
./.venv/bin/python post_tiktok.py ../../3_動画生成/output/本番_015_TikTok.mp4 \
    --caption-file ../投稿予定/本番_015.txt

# 完全自動（確定ボタンまで押す。運用が安定してから）
./.venv/bin/python post_tiktok.py <video> --caption-file <txt> --auto

# バリエーション
./.venv/bin/python post_tiktok.py <video> --caption-file <txt> --schedule-time 07:00
./.venv/bin/python post_tiktok.py <video> --caption-file <txt> --no-bgm --post-now
./.venv/bin/python post_tiktok.py <video> --caption-file <txt> --bgm-volume -18
./.venv/bin/python post_tiktok.py <video> --caption-file <txt> --dry-run
```

> **楽曲名は英訳名と原題の両方で探す**。TikTokは編集画面の曲名を英訳名
> （Ghibli-like piano solo ballad）で出したり原題（ジブリっぽいピアノソロのバラード）で
> 出したりする。英語名だけを見ていた頃、2026-08-20を境に表示が原題へ変わって
> **15本連続でBGM無しのまま公開**された。別名は `editor_ops.BGM_ALIASES` に足す。
> 足せなかった時は `post_log.csv` に `bgm_failed` を残す。

主なオプション: `--no-bgm` / `--bgm-name` / `--bgm-volume`(dB) /
`--post-now`(即時) / `--schedule-time HH:MM` / `--schedule-days N` / `--auto` / `--dry-run`

## キャプション運用
- `4_投稿/投稿予定/<動画名>.txt` に本文（1行目=タイトル、2行目=ハッシュタグ等）を置き `--caption-file` で渡す。
- 末尾に LINE/プロフィール誘導のCTAを入れる（precheckが有無を警告）。将来 2_台本生成 が自動出力。

## ログ
- `4_投稿/ログ/post_log.csv` に追記。status例: `ready_semi_auto` / `予約_semi_auto` /
  `予約_auto` / `precheck_failed` / `session_expired` / `dry_run` など。

---

## YouTube Shorts（公式 Data API v3）

TikTokと違い **公式APIなので規約クリーン・UI変更で壊れない・予約公開が確実**。
`*_YouTube.mp4`（縦9:16）を上げる。1コマンドで
**precheck → アップロード → タイトル/説明/タグ設定 → 翌日6:30に予約公開** まで自動。

### セットアップ（社長が一度だけ・Google Cloud Console / 10〜15分）
1. https://console.cloud.google.com/ でプロジェクト作成（任意名）
2. 「APIとサービス」→「ライブラリ」→ **YouTube Data API v3** を有効化
3. **OAuth同意画面**: User Type=外部 / アプリ名など入力 /
   「テストユーザー」に **投稿用YouTubeを管理するGoogleアカウント** を追加
4. 「認証情報」→「認証情報を作成」→ **OAuthクライアントID** → 種類=**デスクトップアプリ**
5. 作成後の JSON をダウンロードし、このフォルダに **`client_secret.json`** の名前で置く

### 認証（初回のみ・トークン保存）
```bash
./.venv/bin/python youtube_auth.py          # ブラウザが開く→Googleで許可→token保存
./.venv/bin/python youtube_auth.py --check  # 認証済みチャンネル名の疎通確認
```
以後トークンは自動リフレッシュ。パスワードは一切扱わない（TikTokのlogin.pyと同じ思想）。

### 投稿（TikTokと同じキャプションファイルを流用可）
```bash
# 翌日6:30(JST)に予約公開。タイトルはキャプションから自動生成、タグは #… から抽出
./.venv/bin/python youtube_upload.py ../../3_動画生成/output/本番_020_YouTube.mp4 \
    --caption-file ../投稿予定/本番_020.txt

# バリエーション
./.venv/bin/python youtube_upload.py <video> --caption-file <txt> --title "明示タイトル"
./.venv/bin/python youtube_upload.py <video> --caption-file <txt> --publish-now          # 即時公開
./.venv/bin/python youtube_upload.py <video> --caption-file <txt> --privacy unlisted --publish-now  # 限定公開でテスト
./.venv/bin/python youtube_upload.py <video> --caption-file <txt> --dry-run              # 上げずに確認
```
主なオプション: `--title` / `--description(-file)` / `--footer-file`(CTA/リンク) /
`--category`(既定22 People&Blogs, 27=教育) / `--privacy` / `--publish-now` /
`--schedule-time HH:MM(JST)` / `--schedule-days N` / `--dry-run`

- **タイトル**: 未指定なら先頭の【…】か冒頭文＋`#Shorts`（100字以内）。
- **予約公開**: 仕様上 `private` で登録し、指定時刻(JST)に自動公開（`publishAt`）。
- **ログ**: `4_投稿/ログ/youtube_log.csv`（status: `uploaded`/`precheck_failed`/`not_authenticated`/`upload_failed`/`dry_run`）。
- **無料枠**: 1日あたり実質6本相当までアップロード可（1日1本運用には十分）。

### YouTube トラブル時
- **`client_secret.json がありません`** → 上記セットアップでDL・配置。
- **`not_authenticated`** → `youtube_auth.py` を先に実行。
- **403 quotaExceeded** → その日の投稿枠切れ。翌日(太平洋時間リセット)に再実行。
- **予約公開が反映されない** → チャンネル未確認だと予約公開不可のことがある。
  YouTube側でアカウント確認(電話認証)を済ませる。

---

## ファイル
| ファイル | 役割 |
|---|---|
| `precheck.py` | 投稿前チェック（**TikTok/YouTube共通**の土台） |
| **TikTok** | |
| `browser_ctx.py` | Chrome起動(自動化フラグなし)＋CDP接続＋プロファイル管理 |
| `login.py` | 手動ログイン→プロファイル保存 |
| `post_tiktok.py` | メイン投稿（アップロード〜予約の統括） |
| `editor_ops.py` | 編集画面操作（BGM追加・音量-20dB・保存・予約） |
| `inject_caption.py` | 開いている画面のキャプションだけ入れ直す修復用 |
| `.chrome-profile/` | セッション（機密・gitignore） |
| **YouTube** | |
| `youtube_auth.py` | OAuth認証（初回手動→`youtube_token.json`）＋疎通確認 |
| `youtube_upload.py` | メイン投稿（precheck〜API アップロード〜予約公開） |
| `client_secret.json` | OAuthクライアント（社長がDL配置・機密・gitignore） |
| `youtube_token.json` | 認証トークン（自動生成・機密・gitignore） |

## トラブル時（UI変更でどれか転けたら）
- **ログイン画面に飛ぶ** → セッション切れ。`login.py` を再実行。
- **BGM/音量/予約が失敗** → 各ステップは失敗しても続行し、Chromeは開いたまま残るので手動で仕上げ可。
  `editor_ops.py` の該当関数のセレクタ/座標判定を、screenshotを撮りながら直す。
- **予約日付が翌日にならない** → TikTokの既定日付が翌日でない場合は警告が出る。画面で日付を選ぶ。
- **確定ボタンが押せない(--auto)** → アップロード処理中はdisabled。有効化を最大60秒待つ。
