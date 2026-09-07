# LINEbot — スマホのLINEから動画生成〜投稿を回す

> ⚠️ **2026-08-03に [統括/Discordbot](../Discordbot/README.md) へ移行しました。運用はそちらです。**
> この実装は参考・フォールバック用に残しているだけで、日常では起動しません。
> Discord版はトンネル(cloudflared)もwebhook登録も不要になり、さらに
> **毎日18:00にリサーチ→台本→動画生成まで全自動**で走ります。
> なお本実装には既知の不具合があります（`make_video.py --title-lines` を渡していないため
> フックがバラける／TikTokキャプションに長い形式を使う）。Discord版では修正済み。

常時起動のMac上でこのBotを動かすと、**スマホのLINEにメッセージを送るだけ**で
`3_動画生成`（動画生成）と`4_投稿`（TikTok/YouTube予約投稿）を操作できる。
既存スクリプトは一切変更せず、subprocessで呼ぶだけ（手動運用のフォールバックは常に残る）。

## できること（v1）
**手動**
- 「021 作って」→ 台本 `本番_021.txt` から動画2本を生成 → **プレビュー動画＋投稿ボタン**がLINEに届く
- ボタン「TikTokに予約 / YouTubeに予約 / 両方」→ その場で予約投稿（明朝6:30・BGM/音量も既定どおり）
- 投稿はボタンを押した時だけ実行＝**ボタンが社長の承認ゲート**

**自動配信（時間で勝手に走る・設定もLINEだけ）**
- 「毎日20時」→ その時刻に**次の台本を自動生成**し、チャットに「🤖(自動) 本番_014 作るよ」と出て**プレビュー＋確認ボタン**が勝手に届く
- 「自動オフ」→ 止める / 「自動」→ 今の設定と台本キュー残数を表示
- 時刻はLINEで打ち直すだけで変更（Mac/cronは触らない）。**投稿はやはりボタンを押した時だけ**＝押さなければ投稿されない
- 台本は番号順に消化。キューが空なら「台本を用意して」と通知

> LINE公式の"自動配信"機能そのものはBotに跳ね返らない（LINEがBotに通知するのは人がタップ/送信した時だけ）ため、時計はMac上の `scheduler.py` が持つ。ただし設定・操作は全部LINEチャットで完結する。

### v1のスコープ外（＝当面Claude側でやる）
- **台本づくり（AI創作）**。動画化には事前に台本 `3_動画生成/音声/本番_NNN.txt` が必要。
  参考URL→台本化は今まで通りClaudeで作って所定の場所へ置く。将来 Claude API 統合でLINE化予定（v2）。

## しくみ
```
スマホLINE ──メッセージ──▶ LINEサーバ
   │(webhook)                      ▲(reply/push・プレビュー動画配信)
   ▼                               │
 cloudflaredトンネル ──▶ Mac上のFlask(server.py) ──▶ runner.py ──▶ make_video / post_tiktok / youtube_upload
```
- **トンネル**: `cloudflared`のクイックトンネル（アカウント/ポート開放不要）。公開URLは起動ごとに変わるが、
  起動時に **LINEのwebhook先を自動登録**（`line_client.set_webhook_endpoint`）するのでコンソールへの貼り直しは不要。
- **プレビュー**: 完成動画の先頭フレームからポスターjpgを作り、動画は同じトンネル越しにLINEへ配信。
- **認証**: `X-Line-Signature` をチャネルシークレットで検証。`LINE_ALLOWED_USER_ID` を入れれば自分専用にできる。

## 社長の初回準備（1回だけ・10分）
1. **LINE Developers**（https://developers.line.biz/console/）にLINEアカウントでログイン
2. **プロバイダー**を新規作成（名前は任意）
3. その中に **Messaging APIチャネル**を新規作成（アイコン/名前は任意）
4. 「Messaging API設定」タブで:
   - **チャネルアクセストークン（長期）** を発行してコピー
   - 「応答メッセージ」＝**オフ**、「Webhook」＝**オン**（"Webhookの利用"をON）
5. 「チャネル基本設定」タブで **チャネルシークレット** をコピー
6. スマホのLINEで、そのチャネルのQR（Messaging API設定にある）から**Bot（公式アカウント）を友だち追加**
7. Macで設定ファイルを作る:
   ```bash
   cd 統括/LINEbot
   cp .env.example .env
   #  .env を開いて CHANNEL_SECRET と ACCESS_TOKEN を貼る
   ```

> トークン/シークレットは機密。`.env` はgitignore済み（コミットしない）。

## 起動
```bash
cd 統括/LINEbot
./run.sh          # トンネル → webhook自動登録 → 待受
```
起動ログに公開URLと「LINE webhook 自動登録」が出ればOK。あとはスマホのLINEで「021 作って」と送るだけ。

前提: **VOICEVOX.app 起動中／外付けSSD「Extreme SSD」接続**（動画生成の依存）。

## 動作確認（トークン不要のロジックテスト）
```bash
./.venv/bin/python selftest.py
```

## ファイル
| ファイル | 役割 |
|---|---|
| `server.py` | Flask webhook・コマンド解釈・非同期ジョブ・プレビュー配信・起動 |
| `line_client.py` | 署名検証・reply/push・webhook自動登録 |
| `runner.py` | 既存スクリプト（生成/投稿）のsubprocessラッパ |
| `tunnel.py` | cloudflaredクイックトンネル起動＋URL取得 |
| `scheduler.py` | 自動配信の時刻管理（LINEから設定・`schedule.json`に永続化） |
| `config.py` | パス・機密の一元管理（`.env`読込） |
| `selftest.py` | トークン不要の自己テスト |

## トラブル
- **「台本が無い」**: `3_動画生成/音声/本番_NNN.txt` を先に用意。
- **プレビューが届かない**: トンネルが落ちてないか、`/health` が公開URLで200か確認。
- **webhook自動登録に失敗**: 起動ログに出る公開URL+`/callback` をLINEコンソールに手動設定。
- **Macを再起動したら**: `./run.sh` を再実行するだけ（URLは変わるが自動で再登録）。
```
```
