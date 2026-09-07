# プロフリンク クリック計測

TikTok/YouTube のプロフィールに貼る「LINEへのリンク」を自前URLに差し替え、
**何人がクリックしたか**を自分の手元で計測する仕組み。Cloudflare Worker + D1（無料）。

```
プロフ → https://taisyoku-link.<あなた>.workers.dev
        → クリックを1件DBに記録 → アフィリ先のLINEへ即リダイレクト
```

- ✅ **クリック数**（累計 / 今日 / 日別14日 / src別）を計測できる
- ❌ **登録数は取れない**（LINEはアフィリ先のもので中を触れないため）。登録数はアフィリ先の成果レポート側。
- ⚠️ **動画別は自動では不可**：TikTokのプロフリンクは1個で「どの動画から来たか」はリンクに伝わらない。
  `?src=015` タグで区切れるが、それは「その時プロフに貼ってあるURLのタグ」に依存する。日別クリックは正確。

## セットアップ（社長の初回作業）

1. Cloudflare の無料アカウントを作る（https://dash.cloudflare.com/sign-up）
2. ログイン: `npx --yes wrangler@latest login`（ブラウザが開く）
3. デプロイ: `./deploy.sh`
4. 出てきた `https://taisyoku-link.<...>.workers.dev` を **プロフのリンクに設定**

以後、内容を変えたら `./deploy.sh` を再実行するだけ（初回も更新も同じ）。

## 集計を見る

スマホ・PCどちらでも:

```
https://taisyoku-link.<...>.workers.dev/stats?key=cRj5ShK8FVtc
```

`key` は簡易パスワード（`wrangler.toml` の `STATS_KEY`）。知られたら変えて再デプロイ。

## 動画別に測りたいとき（任意）

その動画を推す期間だけ、プロフのURL末尾に `?src=015` を付ける:

```
https://taisyoku-link.<...>.workers.dev/?src=015
```

`/stats` の「src別」に内訳が出る。付けなければ `profile` にまとまる。

## 設定ファイル

| ファイル | 役割 |
|---|---|
| `worker.js` | 本体（計測＋リダイレクト＋/stats画面） |
| `schema.sql` | D1テーブル定義（clicks） |
| `wrangler.toml` | 遷移先LINE URL（`LINE_URL`）と閲覧key（`STATS_KEY`） |
| `deploy.sh` | D1作成→スキーマ適用→デプロイを一括 |

**遷移先のLINEが変わったら** `wrangler.toml` の `LINE_URL` を直して `./deploy.sh`。

## 注意

- `LINE_URL` は現在アフィリ先の直リンクを既定値にしている。プロフの実際のリンクと一致しているか初回に確認すること。
- D1のクリック記録は `ctx.waitUntil` で裏書きするため、ユーザーの体感遅延はほぼゼロ。
