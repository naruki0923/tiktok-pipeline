#!/usr/bin/env bash
# Botを常駐させる（macOSの launchd に登録する）。
#
# 手で起動していると、ターミナルを閉じた・何かの拍子に落ちた、で止まったままになる。
# 2026-09-09の朝はこれで6:00の自動生成が丸ごと飛んだ（issue #3）。環境は全部
# 揃っていて、Botが起動していなかっただけで1日分の打席を落とした。
# launchd に預けると、落ちても30秒で勝手に上がり、Macを再起動しても自分で戻る。
#
#   ./install_agent.sh          登録して起動する（もう入っていれば入れ直す）
#   ./install_agent.sh off      止めて登録を外す
#   ./install_agent.sh status   いま動いているか見る
#
# ⚠ 事前に /bin/bash へ「フルディスクアクセス」が要る。理由は下の FDA の項を参照。
set -euo pipefail
cd "$(dirname "$0")"
DIR="$(pwd -P)"

LABEL="com.taisyoku.discordbot"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
TARGET="gui/$(id -u)/$LABEL"
# ログは Documents の外に置く。launchd はこのプロジェクト（~/Documents 配下）を
# 触れないので、ここを bot.log にすると出力先を開けずに EX_CONFIG で即死する。
LOG="$HOME/Library/Logs/discordbot.log"
# 生死は .bot.pid で見る。`pgrep -f "$DIR/.venv/bin/python"` は当たらない —
# .venv/bin/python は symlink で、ps に出るのは Homebrew 側の実体パスになるため。
bot_pid() {
  local pid
  [ -f "$DIR/.bot.pid" ] || return 1
  pid="$(cat "$DIR/.bot.pid")"
  kill -0 "$pid" 2>/dev/null || return 1
  ps -p "$pid" -o command= 2>/dev/null | grep -q "bot\.py" || return 1
  echo "$pid"
}

fda_help() {
  cat <<MSG

🔴 macOSに止められました（TCC / プライバシー保護）。

   このプロジェクトは ~/Documents の下にある。ターミナルから手で動かす分には
   通るが、**launchd から起動したプロセスは ~/Documents を一切触れない**。
   確認画面も出ずに「Operation not permitted」で落ちるだけなので分かりにくい。

   一度だけ、GUIで許可を出してください（社長の操作・パスワードが要ります）:

     システム設定 → プライバシーとセキュリティ → フルディスクアクセス
       → 「+」→ Cmd+Shift+G で  /bin/bash  を指定して追加 → スイッチをオン

   そのあと、もう一度  ./install_agent.sh  を実行してください。

   （許可を出すまでは、これまでどおり ./run.sh で手起動して運用できます。
     ただし落ちても勝手には上がりません）
MSG
}

case "${1:-install}" in
  status)
    if launchctl print "$TARGET" >/dev/null 2>&1; then
      echo "🟢 常駐に登録済み"
      launchctl print "$TARGET" | grep -E "^[[:space:]]+(state|pid|last exit code) =" || true
    else
      echo "⚪️ 未登録（./install_agent.sh で登録できます）"
    fi
    if pid="$(bot_pid)"; then
      echo "🟢 Bot稼働中 (PID $pid)"
    else
      echo "🔴 Botは動いていません"
    fi
    echo "   ログ: $LOG と $DIR/bot.log"
    exit 0
    ;;
  off)
    launchctl bootout "$TARGET" 2>/dev/null || true
    rm -f "$PLIST"
    echo "⚪️ 常駐を外しました（Botも止まります）"
    exit 0
    ;;
esac

# launchd が子プロセスに渡す PATH は /usr/bin:/bin:/usr/sbin:/sbin だけ。ffmpeg も
# claude CLI も Homebrew 側に居るので、そのままだと「Botは起動するのに動画生成だけ
# 落ちる」という分かりにくい壊れ方をする。実際に使うコマンドの置き場所を調べて足す。
BASE="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
EXTRA=""
for cmd in ffmpeg ffprobe claude codex yt-dlp; do
  p="$(command -v "$cmd" 2>/dev/null || true)"
  [ -n "$p" ] || continue
  d="$(dirname "$p")"
  case ":$BASE$EXTRA:" in
    *":$d:"*) ;;
    *) EXTRA="$EXTRA:$d" ;;
  esac
done
BOT_PATH="$BASE$EXTRA"

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
# run.sh を直接 Program にせず /bin/bash 経由で起動する。フルディスクアクセスは
# 「実行されるバイナリ」に対して与えるもので、~/Documents の中のスクリプトは
# そもそも exec できないため、許可を与えられる /bin/bash に噛ませる必要がある。
cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>$LABEL</string>
	<key>ProgramArguments</key>
	<array>
		<string>/bin/bash</string>
		<string>$DIR/run.sh</string>
	</array>
	<key>EnvironmentVariables</key>
	<dict>
		<key>PATH</key>
		<string>$BOT_PATH</string>
		<key>LANG</key>
		<string>ja_JP.UTF-8</string>
		<key>PYTHONUTF8</key>
		<string>1</string>
	</dict>
	<key>RunAtLoad</key>
	<true/>
	<key>KeepAlive</key>
	<true/>
	<key>ThrottleInterval</key>
	<integer>30</integer>
	<key>StandardOutPath</key>
	<string>$LOG</string>
	<key>StandardErrorPath</key>
	<string>$LOG</string>
</dict>
</plist>
PLIST_EOF

launchctl bootout "$TARGET" 2>/dev/null || true
: > "$LOG"
launchctl bootstrap "gui/$(id -u)" "$PLIST"

# 本当に上がったところまで見届ける。「登録できました」と言いながら
# 一度も起動していない、という今回いちばん困った状態を作らないため。
for _ in $(seq 1 20); do
  bot_pid >/dev/null && break
  grep -q "Operation not permitted" "$LOG" 2>/dev/null && break
  sleep 1
done

if grep -q "Operation not permitted" "$LOG" 2>/dev/null; then
  launchctl bootout "$TARGET" 2>/dev/null || true
  fda_help
  exit 1
fi

if bot_pid >/dev/null; then
  echo "🟢 常駐に登録して起動しました: $PLIST"
  echo "   PATH: $BOT_PATH"
  echo "   ログ: $LOG"
  echo "   状態を見る: ./install_agent.sh status ／ 外す: ./install_agent.sh off"
else
  echo "🟡 登録はしたが、まだ起動を確認できていません。$LOG の末尾を見てください。" >&2
  launchctl print "$TARGET" | grep -E "^[[:space:]]+(state|last exit code) =" || true
  exit 1
fi
