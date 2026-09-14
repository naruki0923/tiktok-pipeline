# 統括/tiktok_api — TikTok Display API（自分の全動画の再生数）

Studio の CSV は上位15本しか出ない（issue #16）ので、公式 API で全動画を取るための一式。

- `tiktok_api.py` … `auth`（1回。運用垢でログインして許可）/ `fetch`（取込/ に Content_api_*.csv を書く）
- `site/` … GitHub Pages（`gh-pages` ブランチ）に置いている公開ページの元。利用規約・プライバシー・
  ログインボタン付きトップ・OAuth の callback・TikTok の URL 検証ファイル。
  変更したら `gh-pages` ブランチへコピーして push（URL: https://naruki0923.github.io/tiktok-pipeline/）
- `app_icon_1024.png` … 申請用アイコン
- `申請メモ.md` … developers.tiktok.com に入れた内容の控え（Production は保存できていないので消えたらここから）

## 状態（2026-09-13）
- アプリ「Video Stats Sync」作成済み。Sandbox「demo」は設定済み（Login Kit + video.list）
- 残り: Sandbox の Target user に運用垢を追加 → `.env` に Sandbox の key/secret → `auth` → `fetch` →
  その流れを録画 → Production にデモ動画を付けて Submit for review（社長が押す）
- 審査が通ったら `.env` の key/secret を Production の値に差し替えて `auth` をやり直す

## .env（プロジェクト直下）
```
TIKTOK_CLIENT_KEY=
TIKTOK_CLIENT_SECRET=
```
