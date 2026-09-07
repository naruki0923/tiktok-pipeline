#!/usr/bin/env bash
# プロフ用リンク計測Workerのデプロイ（初回も更新も同じコマンドでOK）
#   前提: Cloudflare無料アカウント + Node.js
#   初回のみ:  npx wrangler login   （ブラウザでログイン）
#   デプロイ:  ./deploy.sh
set -euo pipefail
cd "$(dirname "$0")"

WR="npx --yes wrangler@latest"

echo "▶ D1データベースを用意…"
# 既にあれば作成はスキップされる。database_id を取り出して wrangler.toml に埋める。
CREATE_OUT=$($WR d1 create taisyoku-clicks 2>&1 || true)
echo "$CREATE_OUT"

# database_id を取得（新規作成の出力 or 既存一覧のどちらからでも）
DB_ID=$(printf '%s\n' "$CREATE_OUT" | grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | head -1 || true)
if [ -z "${DB_ID:-}" ]; then
  DB_ID=$($WR d1 list --json 2>/dev/null | python3 -c "import sys,json;[print(d['uuid']) for d in json.load(sys.stdin) if d['name']=='taisyoku-clicks']" | head -1 || true)
fi
if [ -z "${DB_ID:-}" ]; then
  echo "✗ database_id を取得できませんでした。'npx wrangler login' 済みか確認してください。"; exit 1
fi
echo "  database_id = $DB_ID"

# wrangler.toml のプレースホルダを実IDに置換（macOS sed）
sed -i '' -E "s/^database_id = .*/database_id = \"$DB_ID\"/" wrangler.toml

echo "▶ テーブル作成（schema.sql・冪等）…"
$WR d1 execute taisyoku-clicks --remote --file schema.sql

echo "▶ デプロイ…"
$WR deploy

echo ""
echo "✅ 完了。以下をTikTok/YouTubeのプロフリンクに設定してください。"
echo "   計測URL(プロフ用) : 上の 'https://taisyoku-link.<あなた>.workers.dev' を使用"
echo "   集計を見る        : 同URL + '/stats?key=cRj5ShK8FVtc'"
echo "   動画別に測るなら  : URL末尾に '?src=015' を付ける"
