"""Discord Bot 本体。LINE版の置き換え。

LINE版と違い**トンネル(cloudflared)もwebhook登録も要らない**。Discordのbotは
こちらから常時接続を張るので、Macがネットに繋がっていれば動く。

対応（指定チャンネルにテキストを送るだけ）:
  「作って」                 → リサーチから全自動で1本作る（18:00の自動実行と同じ）
  「035 作って」             → 台本 本番_035.txt から動画だけ作る
  TikTokのURLを貼る          → その動画を参考に台本化→動画生成
  「投稿」                   → YouTubeを予約投稿し、TikTokは貼る投稿文を出す
  「投稿 035」「035 投稿」    → 指定した回を投稿
  「毎日18時」「自動オフ」「自動」 → 自動実行の時刻設定・停止・状態
  「分析」「毎週日曜22時」「週次オフ」 → 週次レビュー（分析→検索語・台本方針の更新）
  「ヘルプ」「状態」          → 使い方

**投稿は社長が「投稿」と言った時（またはボタンを押した時）だけ実行する**。
自動実行がやるのは動画を作って見せるところまで。

作る側がコケた時は社長を待たない。Codexに直させ、直ったコードを読み直して
**自動でもう一度作る**（config.AUTO_RETRY_MAX 回まで）。投稿は対象外。

**動画1本＝スレッド1つ**。チャンネルには「🤖 06:30 自動実行」のような見出しが
1行残るだけで、進捗・プレビュー・投稿結果はぜんぶその中に入る。
毎朝の分が同じチャンネルに流れて混ざらないようにするため。
スレッドの中でそのまま「投稿」「直して」と話しかけられる。
"""
from __future__ import annotations

import asyncio
import importlib
import re
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import discord
from discord.ext import tasks

import config
import codex_rescue
import pipeline
import runner

intents = discord.Intents.default()
intents.message_content = True          # 開発者ポータルで MESSAGE CONTENT INTENT を有効にすること
bot = discord.Client(intents=intents)

_busy = asyncio.Lock()                  # 生成と投稿を同時に走らせない（Chrome/VOICEVOXが競合する）
_codex_busy = asyncio.Lock()            # 同時エラーでCodexを複数起動しない

HELP = """**📱 使い方**
**作る**
・`作って` … リサーチから全自動で1本（毎日18:00に勝手に走るのと同じ）
・`035 作って` … 用意済みの台本 本番_035.txt から動画だけ作る
・TikTokのURLを貼る … その動画を参考にして台本化→動画生成

**見る・直す**
・`見せて` … 直近の完成動画をもう一度送る　・`036 見せて` … 回を指定
・`直して <どう直すか>` … 台本を手直しして動画を作り直す
　例: `直して タイトルをもっと短く` / `036 直して 3つ目の項目を消して2つにして`
　※直せるのは台本と動画。仕組み自体の不具合はClaudeに言ってね

**出す**（※ここだけは社長の合図が要る）
・`投稿` … **YouTubeは翌朝6:30に予約**／**TikTokは投稿文を出すだけ**
　→ TikTokは社長がアプリで手動投稿。出てきた投稿文をコピーして貼るだけ（目安 翌朝8:30）
・`おまかせ投稿` … TikTokもブラウザ自動化で確定まで押す（Macの前に居られない時用）
・`投稿 035` … 回を指定　・`夜 投稿` … 翌朝でなく次の18:30に予約

**自動実行**
・`毎日18時` … 時刻を変更　・`自動オフ` … 停止　・`自動` … いまの設定
・生成がコケた時は Codex が直して**自動でもう一度作る**（最大3回）。
　社長が `作って` と言い直さなくてOK。※**投稿だけは今までどおり合図が要る**

**分析（毎週日曜22:00に勝手に走る）**
・`分析` … 今すぐ週次レビュー。実測を取り直し、**検索語と台本の重点方針を更新**
・`毎週日曜22時` … 曜日と時刻を変更　・`週次オフ` … 停止　・`週次` … いまの設定
　※更新されるのは検索語と方針まで。ネタ提案は下書きで、採用/却下は社長

**スレッド**
・動画1本ごとにスレッドが立つ（例 `08/05 #037 退職前にやるべき…`）
・チャンネルに残るのは見出し1行だけ。中身はスレッドを開いてね
・スレッドの中で `投稿` `直して〜` とそのまま話しかけてOK
"""


# --- 状態 -------------------------------------------------------------------
def _auto() -> dict:
    s = runner.load_state()
    a = {"enabled": config.AUTO_ENABLED, "time": config.AUTO_TIME, "last_fired": ""}
    a.update(s.get("auto", {}))
    return a


def _save_auto(**fields) -> None:
    s = runner.load_state()
    s.setdefault("auto", {}).update(fields)
    runner.save_state(s)


DOW_JA = "月火水木金土日"          # datetime.weekday() の並び（0=月 … 6=日）


def _weekly() -> dict:
    w = {"enabled": config.WEEKLY_ENABLED, "dow": config.WEEKLY_DOW,
         "time": config.WEEKLY_TIME, "last_fired": ""}
    w.update(runner.load_state().get("weekly", {}))
    return w


def _save_weekly(**fields) -> None:
    s = runner.load_state()
    s.setdefault("weekly", {}).update(fields)
    runner.save_state(s)


def _channel() -> discord.abc.Messageable | None:
    cid = config.CHANNEL_ID or runner.load_state().get("channel_id", 0)
    return bot.get_channel(int(cid)) if cid else None


def _home_id(ch: discord.abc.Messageable) -> int:
    """スレッド内なら親チャンネルのIDを返す（許可判定はいつも親で行う）。"""
    return ch.parent_id if isinstance(ch, discord.Thread) else getattr(ch, "id", 0)


def auto_status() -> str:
    a = _auto()
    latest = runner.latest_ready() or "なし"
    return (f"⏰ 自動実行: {'🟢オン' if a['enabled'] else '⚪️オフ'} / 毎日 {a['time']}\n"
            f"🎬 直近の完成動画: {latest}")


def weekly_status() -> str:
    w = _weekly()
    return (f"📊 週次レビュー: {'🟢オン' if w['enabled'] else '⚪️オフ'} / "
            f"毎週{DOW_JA[int(w['dow']) % 7]}曜 {w['time']}\n"
            "　→ 実測を取り直し、検索語と台本の重点方針を更新（**投稿はしない**）")


# --- ボタン -----------------------------------------------------------------
BTN_LABEL = {("tiktok", False): "TikTok文", ("youtube", False): "YouTube",
             ("both", False): "YouTube＋TikTok文", ("both", True): "おまかせ"}


class PostButton(discord.ui.DynamicItem[discord.ui.Button],
                 template=r"post:(?P<where>\w+):(?P<slot>\w+):(?P<auto>[01]):(?P<name>.+)"):
    """押した瞬間が承認ゲート。where=tiktok/youtube/both, slot=am/pm。

    auto=False … TikTokは触らず投稿文だけ出す（既定の運用）。YouTubeは予約する
    auto=True  … TikTokもブラウザ自動化で確定まで押す（Macの前に居られない時用）

    **DynamicItem にしてあるのはBotを再起動してもボタンを生かすため**。
    ふつうの View はBotのメモリ上にしか無いので、再起動すると過去のプレビューの
    ボタンが全部死んで「時間内に応答しませんでした」になる（2026-08-05に踏んだ）。
    DynamicItem は custom_id を template で照合して押された時に組み立て直すので、
    何度再起動しても、いつのプレビューのボタンでも効く。
    """

    def __init__(self, name: str, where: str, slot: str, auto: bool, row: int = 0):
        tag = "朝" if slot == "am" else "夜"
        super().__init__(discord.ui.Button(
            label=f"{tag}｜{BTN_LABEL.get((where, auto), where)}", row=row,
            style=discord.ButtonStyle.danger if auto else discord.ButtonStyle.primary,
            custom_id=f"post:{where}:{slot}:{int(auto)}:{name}"))
        self.vname, self.where, self.slot, self.auto = name, where, slot, auto

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction,
                             item: discord.ui.Button, match: re.Match[str]):
        """再起動後に押された時、custom_id からボタンを復元する。"""
        return cls(match["name"], match["where"], match["slot"], match["auto"] == "1")

    async def callback(self, interaction: discord.Interaction) -> None:
        mode = "おまかせ" if self.auto else "TikTokは手動"
        await interaction.response.send_message(
            f"📤 {self.vname} → {self.where}"
            f"（{runner.slot_label(self.slot, self.where)}・{mode}）")
        ch = await route(interaction.channel, self.vname)
        await do_post(ch, self.vname, self.where, self.slot, self.auto)


# 押された custom_id を PostButton に振り分ける登録。これが無いと再起動後は無反応になる
bot.add_dynamic_items(PostButton)


def post_view(name: str) -> discord.ui.View:
    """上段＝翌朝（TikTok 8:30/YouTube 6:30） / 下段＝夜18:30。

    青いボタンはTikTokに触らず投稿文を出すだけ。赤（おまかせ）だけが
    ブラウザ自動化で確定まで押す。実際の時刻は runner.SLOTS が正。
    """
    v = discord.ui.View(timeout=None)
    for row, slot in ((0, "am"), (1, "pm")):
        for where, auto in (("tiktok", False), ("youtube", False),
                            ("both", False), ("both", True)):
            v.add_item(PostButton(name, where, slot, auto, row))
    return v


# --- メンション --------------------------------------------------------------
def mention() -> str:
    """名指し用の `<@id> `。相手が分からなければ空文字（＝ただの通知）。

    動画が出来て**あとは投稿だけ**の状態はスマホに気づいてほしいので、
    そこだけ名指しする。相手は .env（DISCORD_MENTION_USER_ID か
    DISCORD_ALLOWED_USER_ID）が優先で、無ければ最初に話しかけてきた人を
    state.json に覚えて使うので、設定しなくても2回目以降は効く。
    """
    uid = config.MENTION_USER_ID or runner.load_state().get("owner_id", 0)
    return f"<@{int(uid)}> " if uid else ""


async def learn_owner() -> None:
    """起動時、まだ相手を知らなければチャンネルの直近の発言者を社長として覚える。

    これが無いと「更新後まだ一度も話しかけていない」朝の自動実行だけ
    名指しが飛ばない、という取りこぼしが起きる。
    """
    if config.MENTION_USER_ID or runner.load_state().get("owner_id"):
        return
    ch = _channel()
    if not isinstance(ch, discord.TextChannel):
        return
    try:
        async for m in ch.history(limit=50):
            if not m.author.bot:
                s = runner.load_state()
                s["owner_id"] = m.author.id
                runner.save_state(s)
                print(f"✔ メンション先を覚えました: {m.author}")
                return
    except discord.HTTPException as e:
        print(f"[mention] 履歴を読めませんでした（『メッセージ履歴を読む』権限）: {e}")


# --- 送信ヘルパ --------------------------------------------------------------
async def send(ch: discord.abc.Messageable, text: str, **kw) -> None:
    """Discordの2000字上限で切って送る。"""
    for i in range(0, max(len(text), 1), 1900):
        await ch.send(text[i:i + 1900] or "…", **({} if i else kw))


# --- スレッド ---------------------------------------------------------------
# 動画1本＝スレッド1つ。チャンネルには見出し1行だけ残り、進捗・プレビュー・
# 投稿結果はすべてその中に入る。同じチャンネルに全部が流れて
# 「いつがどの動画か分からない」状態を避けるため。
THREAD_KEEP = 10080          # スレッドが一覧から畳まれるまで（分）＝7日。
_warned_no_thread = False    # 権限不足の警告は1回だけ出す
# スレッドID → チャンネルに残した見出しメッセージ。回番号が決まったら書き足す。
# スレッド名の付け替えには『スレッドの管理』権限が要ることがあるが、
# **自分が出したメッセージの編集はどんな権限でも通る**ので、一覧で見分けが
# つく情報は必ずこちら（見出し）に載せる。名前の付け替えはできれば、の扱い。
_anchors: dict[int, discord.Message] = {}


def thread_title(name: str, title: str = "") -> str:
    """スレッド名。日付＋回番号が先頭に来るので一覧で日付順に読める。"""
    head = f"{datetime.now():%m/%d} {name.replace('本番_', '#')}"
    return (f"{head} {title}".strip())[:100]


async def thread_for(name: str) -> discord.Thread | None:
    """その回のスレッド。畳まれていても fetch で拾える（書けば自動で開く）。"""
    tid = runner.load_state().get("videos", {}).get(name, {}).get("thread_id")
    if not tid:
        return None
    th = bot.get_channel(int(tid))
    if th is None:
        try:
            th = await bot.fetch_channel(int(tid))
        except discord.HTTPException:
            return None
    return th if isinstance(th, discord.Thread) else None


async def open_thread(ch: discord.abc.Messageable, headline: str,
                      title: str) -> discord.abc.Messageable:
    """見出しをチャンネルに出し、それにぶら下げたスレッドを返す。

    立てられない時（権限が無い・すでにスレッド内・DM）は ch をそのまま返して
    従来どおりチャンネルに直接書く。スレッドが作れないだけで作業は止めない。
    """
    global _warned_no_thread
    if isinstance(ch, discord.Thread) or not isinstance(ch, discord.TextChannel):
        if headline:
            await ch.send(headline)
        return ch
    anchor = await ch.send(headline or "🎬 作業を始めます")
    try:
        th = await anchor.create_thread(name=title, auto_archive_duration=THREAD_KEEP)
        _anchors[th.id] = anchor
        return th
    except discord.HTTPException as e:
        print(f"[thread] 作成できないのでチャンネルに直接書きます: {e}")
        if not _warned_no_thread:
            _warned_no_thread = True
            await ch.send("⚠️ スレッドを作れないのでこのチャンネルに直接書きます。"
                          "Discordのサーバー設定でBotに『パブリックスレッドの作成』と"
                          "『スレッドでメッセージを送信』を許可してね")
        return ch


async def work_thread(ch: discord.abc.Messageable, mode: str, arg: str,
                      headline: str) -> discord.abc.Messageable:
    """作業1件ぶんの書き込み先。既にその回のスレッドがあれば作らず再利用する。"""
    # 新しい1本は必ず親チャンネルに新しいスレッドを立てる（スレッド内で「作って」と
    # 言われても、別の回の話がそのスレッドに混ざらないように）
    if mode in ("auto", "url") and isinstance(ch, discord.Thread) and ch.parent is not None:
        ch = ch.parent

    known = arg.partition("\n")[0] if mode == "revise" else (arg if mode == "name" else "")
    if known:
        th = await thread_for(known)
        if th is not None:
            await th.send(headline or "🎬 作り直します")
            return th
    # auto/url は完成するまで回番号が分からないので仮の名前で立てて、後で付け替える
    prov = {"auto": "生成中…（リサーチから）", "url": "生成中…（参考URL）",
            "revise": "手直し中…"}.get(mode, "生成中…")
    return await open_thread(ch, headline,
                             thread_title(known, prov) if known
                             else f"{datetime.now():%m/%d} {prov}"[:100])


async def bind_thread(ch: discord.abc.Messageable, name: str, title: str = "") -> None:
    """回番号が決まったスレッドに正式な名前を付け、state に紐づけて覚える。

    以降 `投稿 036` のようにチャンネルで言われても、この回の話はこのスレッドに戻せる。
    """
    if not isinstance(ch, discord.Thread):
        return
    runner.remember(name, thread_id=ch.id)

    # ① チャンネルの見出しに回番号を書き足す（自分のメッセージなので必ず通る）。
    #    チャンネルを上から眺めるだけで「いつの何番か」が分かる状態を作るのはここ。
    anchor = _anchors.pop(ch.id, None)
    if anchor is not None:
        try:
            await anchor.edit(content=f"{anchor.content}\n"
                                      f"└ ✅ **{name}**{(' ' + title) if title else ''}")
        except discord.HTTPException as e:
            print(f"[thread] 見出しを更新できませんでした: {e}")

    # ② スレッド名も揃える。『スレッドの管理』権限が無いと弾かれるが、①があるので実害はない
    want = thread_title(name, title)
    if ch.name != want:
        try:
            await ch.edit(name=want)
        except discord.HTTPException as e:      # 名前変更は10分に2回まで
            print(f"[thread] 名前を変えられませんでした（『スレッドの管理』権限）: {e}")


async def route(ch: discord.abc.Messageable, name: str) -> discord.abc.Messageable:
    """その回のスレッドがあればそちらへ回す（チャンネルには行き先だけ置く）。"""
    if isinstance(ch, discord.Thread):
        return ch
    th = await thread_for(name)
    if th is None:
        return ch
    await ch.send(f"↪️ {name} のスレッド {th.mention} で続けます")
    return th


def make_logger(ch: discord.abc.Messageable, loop: asyncio.AbstractEventLoop):
    """別スレッドで走る pipeline から進捗をチャンネルへ流すための橋渡し。"""
    def log(msg: str) -> None:
        asyncio.run_coroutine_threadsafe(send(ch, msg), loop)
    return log


# Codexが実際にコードを直したか見分けるために見張る場所（.venv等は除く）
CODE_DIRS = ("統括/Discordbot", "1_リサーチ/scripts", "2_台本生成/scripts",
             "3_動画生成/scripts", "4_投稿/scripts")


def code_snapshot() -> dict[str, float]:
    """各工程スクリプトの更新時刻。Codexの前後で比べて「直ったか」を見る。"""
    snap: dict[str, float] = {}
    for rel in CODE_DIRS:
        for p in (config.ROOT / rel).rglob("*.py"):
            if ".venv" in p.parts or "__pycache__" in p.parts:
                continue
            try:
                snap[str(p)] = p.stat().st_mtime
            except OSError:
                pass
    return snap


def reload_code(changed: list[str]) -> str:
    """Codexが直した Discordbot 側のモジュールを、Botを止めずに読み直す。

    各工程スクリプト（auto_research.py 等）は subprocess なので毎回新しいものが
    動くが、このBotが import している config/runner/pipeline は**起動時のまま**残る。
    ここを読み直さないと「直したのに同じエラーで落ちる」ことになる（2026-08-17）。
    戻り値は人へ伝えたい注意書き（無ければ空文字）。
    """
    mine = [p for p in changed if Path(p).parent == config.BOT_DIR]
    if not mine:
        return ""
    note = ""
    if any(Path(p).name == "bot.py" for p in mine):
        # bot.py 自身は動いたまま差し替えられない（イベントハンドラごと入れ替わるため）
        note = ("⚠️ Bot本体(bot.py)が変更されました。**再起動するまで反映されません**"
                "（今回の作り直しは変更前のBotのまま走ります）")
    try:
        for mod in (config, codex_rescue, runner, pipeline):
            importlib.reload(mod)
    except Exception:  # noqa: BLE001 - 直後の構文エラー等。古いモジュールのまま続ける
        detail = traceback.format_exc()[-600:]
        print(f"[reload] 読み直しに失敗\n{detail}")
        return f"⚠️ 修正後のコードを読み直せませんでした\n```\n{detail}\n```"
    print(f"[reload] 読み直しました: {', '.join(Path(p).name for p in mine)}")
    return note


async def ask_codex(ch: discord.abc.Messageable | None, context: str,
                    error: str) -> tuple[bool, list[str]]:
    """工程エラーを契約済みCodex CLIへ渡し、調査・修正結果をDiscordへ返す。

    戻り値は (Codexが正常終了したか, 書き換わったファイル)。呼び出し側は
    後者を見て「直ったから作り直す／直っていないので待つ」を決める。
    """
    if not config.AUTO_CODEX_RESCUE:
        return False, []
    if _codex_busy.locked():
        if ch:
            try:
                await ch.send("🛠 Codexは別のエラーを対応中です。このエラーもログに残しました")
            except discord.HTTPException:
                pass
        print(f"[codex-rescue/queued] {context}\n{error[-3000:]}")
        return False, []
    async with _codex_busy:
        try:
            if ch:
                try:
                    await ch.send("🛠 エラーを検知したので、Codexを起動して原因調査・修正を始めます…")
                except discord.HTTPException as e:
                    print(f"[codex-rescue] Discordへ開始通知できません: {e}")
            before = await asyncio.to_thread(code_snapshot)
            ok, report = await asyncio.to_thread(codex_rescue.run, context, error)
            after = await asyncio.to_thread(code_snapshot)
            changed = sorted(k for k, v in after.items() if before.get(k) != v)
            icon = "✅" if ok else "⚠️"
            if ch:
                try:
                    await send(ch, f"{icon} **Codexの対応結果**\n{report[-5000:]}")
                except discord.HTTPException as e:
                    print(f"[codex-rescue] Discordへ結果を送れません: {e}")
            print(f"[codex-rescue] {context}: {report}")
            return ok, changed
        except Exception:  # 救援機能自身の失敗でBotまで落とさない
            print(f"[codex-rescue] 起動または通知に失敗\n{traceback.format_exc()}")
            return False, []


async def send_preview(ch: discord.abc.Messageable, name: str, head: str,
                       ping: bool = False) -> None:
    """完成動画のプレビュー＋投稿ボタンを送る。

    ping=True で社長を名指しする。動画が出来た＝あとは投稿の合図を待つだけなので、
    自動実行（18:00）で勝手に出来た時はスマホに通知が飛ぶようにしておく。
    自分で「見せて」と言った時は目の前に居るので鳴らさない。
    """
    hook = "／".join(runner.hook_block(name))
    body = (f"{mention() if ping else ''}{head}"
            f"\n🔖 タイトル（{runner.title_lines_for(name)}行）: {hook}"
            f"\n`投稿` → YouTubeは予約、TikTokは**貼る投稿文が出る**（手動投稿）。"
            "\n（下のボタンでも同じことができます。赤＝TikTokもおまかせ）")
    # タイトルカードの静止画は**必ず**付ける。1枚見れば崩れが分かるので、
    # 動画を再生しなくても確認できる（タイトルが一番壊れやすいため）。
    files = []
    poster = await asyncio.to_thread(runner.poster_jpg, name)
    if poster:
        files.append(discord.File(str(poster), filename=f"{name}_title.jpg"))
    prev = await asyncio.to_thread(runner.preview_mp4, name)
    if prev:
        files.append(discord.File(str(prev), filename=f"{name}.mp4"))
    else:
        body += f"\n（動画が大きすぎて添付できません。実物: `{runner.tiktok_mp4(name)}`）"
    await ch.send(body, files=files, view=post_view(name))


# --- 仕事 -------------------------------------------------------------------
async def make_once(th: discord.abc.Messageable, mode: str, arg: str,
                    log) -> tuple[dict | None, str, str]:
    """動画を1本ぶん作る（1回だけ）。

    失敗したら (None, Codexに渡す文脈, エラー全文) を返す。文脈が空の失敗は
    「作り直しても同じ」＝人がやることがある失敗なので、呼び出し側は再試行しない。
    """
    try:
        if mode == "auto":
            res = await asyncio.to_thread(pipeline.run_auto, log)
        elif mode == "url":
            res = await asyncio.to_thread(pipeline.run_from_url, arg, log)
        elif mode == "revise":
            target, _, instruction = arg.partition("\n")
            res = await asyncio.to_thread(pipeline.run_revise, target, instruction, log)
        else:
            if not runner.script_txt(arg).exists():
                await th.send(f"⚠️ 台本が無いよ: {arg}.txt")
                return None, "", ""          # 台本を置くのは人の仕事。作り直さない
            await th.send(f"🎬 {arg} 生成中（数分待ってね）…")
            ok, vlog = await asyncio.to_thread(runner.make_video, arg)
            if not ok:
                await send(th, f"❌ {arg} の生成に失敗\n```\n{vlog[-1200:]}\n```")
                return None, f"{arg} の動画生成", vlog
            res = {"name": arg, "title": "", "angle": ""}
    except pipeline.PipelineError as e:
        await send(th, f"❌ {e}")
        return None, f"動画作成（{mode}）", str(e)
    except Exception:  # noqa: BLE001 - 予期しない失敗も必ず通知する
        detail = traceback.format_exc()
        await send(th, f"❌ 想定外のエラー\n```\n{detail[-1200:]}\n```")
        return None, f"動画作成（{mode}）の想定外エラー", detail
    return res, "", ""


async def do_make(ch: discord.abc.Messageable, mode: str, arg: str = "",
                  headline: str = "") -> None:
    """動画を作る。mode = auto（リサーチから）/ url / name（台本指定）。

    進捗・完成プレビューは1本ごとのスレッドに書く。チャンネルに残るのは
    headline の1行だけなので、あとから「いつのどの動画か」が一覧で追える。

    **コケたら社長を待たずに作り直す**（2026-08-17 社長判断）。Codexに直させて
    → 直ったコードを読み直して → もう一度。AUTO_RETRY_MAX 回まで。
    投稿はこの対象外で、今までどおり社長の合図が要る。
    """
    if _busy.locked():
        await ch.send("⏳ いま別の処理が走っています。終わるまで待ってね")
        return
    async with _busy:
        th = await work_thread(ch, mode, arg, headline)
        log = make_logger(th, asyncio.get_running_loop())
        for attempt in range(config.AUTO_RETRY_MAX + 1):
            res, context, detail = await make_once(th, mode, arg, log)
            if res is not None:
                break
            if not context:
                return                       # 人がやることがある失敗。作り直さない
            _, changed = await ask_codex(th, context, detail)
            if attempt >= config.AUTO_RETRY_MAX:
                await th.send(f"🛑 {attempt + 1}回試したのでいったん止めます。"
                              "直っていそうなら `作って` でもう一度動かしてね")
                return
            note = await asyncio.to_thread(reload_code, changed) if changed else ""
            if note:
                await send(th, note)
            # コードが直っていれば即やり直す。直っていない（TikTok側の一時制限など）時は
            # 間を空ける。作り直しの間、他のコマンドは待たせる（Chrome/VOICEVOXの競合防止）。
            wait = 0 if changed else config.AUTO_RETRY_WAIT
            await th.send(f"🔁 自動で作り直します（{attempt + 2}回目/最大"
                          f"{config.AUTO_RETRY_MAX + 1}回）"
                          + (f"。{wait // 60}分ほど待ってから始めます" if wait else "")
                          + ("\n（この間の他のコマンドは終わるまで待たせます）" if wait else ""))
            if wait:
                await asyncio.sleep(wait)
        else:
            return

        # 回番号が確定したのでスレッド名を付け替え、以降この回の話はここに集める
        await bind_thread(th, res["name"], res.get("title", ""))
        head = (f"✅ **{res['name']}** 完成 — **あとは投稿だけ**です\n"
                f"{('📌 ' + res['angle']) if res.get('angle') else ''}"
                f"{chr(10) + '🔗 参考: ' + res['ref_url'] if res.get('ref_url') else ''}")
        # 出来上がりは社長を名指しして知らせる（スマホの通知で気づけるように）
        await send_preview(th, res["name"], head, ping=True)


async def _do_post_inner(ch: discord.abc.Messageable, name: str, where: str, slot: str,
                         auto: bool = False) -> None:
    """既定（auto=False）は **YouTubeだけ予約し、TikTokは投稿文を出すだけ**。

    TikTokは手動投稿に切り替えた（2026-09-09 社長判断）。`おまかせ投稿`
    （auto=True）と言われた時だけ、従来どおりブラウザ自動化で確定まで押す。
    """
    if _busy.locked():
        await ch.send("⏳ いま別の処理が走っています。終わるまで待ってね")
        return
    async with _busy:
        # 予約時刻は媒体で違う（TikTok 8:30 / YouTube 6:30）ので、それぞれの時刻で言う
        tk_when, yt_when = runner.slot_label(slot, "tiktok"), runner.slot_label(slot, "youtube")
        out, needs_hand, errors = [], False, []

        # TikTokは既定で手動投稿（2026-09-09 社長判断）。Botはブラウザを触らず、
        # 貼る文だけ渡す。ブラウザ自動化を使うのは `おまかせ投稿`（auto=True）の時だけ。
        tiktok_manual = where in ("tiktok", "both") and not auto

        if where in ("tiktok", "both") and auto:
            status, log = await asyncio.to_thread(runner.post_tiktok, name, True, slot)
            if status == "confirmed":
                out.append(f"✅ TikTok: 予約完了（{tk_when}）")
            elif status == "awaiting":
                needs_hand = True
                msg = (f"🖐 TikTok: 設定まで完了（{tk_when}）。**まだ投稿されていません**\n"
                       f"　Chromeの画面で【投稿予約する】を押してください")
                for note in runner.manual_fixups(log):
                    msg += f"\n　{note}"
                out.append(msg)
            else:
                out.append(f"❌ TikTok: 失敗\n```\n{log[-900:]}\n```")
                errors.append(f"TikTok投稿処理:\n{log}")

        if where in ("youtube", "both"):
            ok, log = await asyncio.to_thread(runner.post_youtube, name, slot)
            out.append(f"✅ YouTube: 予約完了（{yt_when}）" if ok
                       else f"❌ YouTube: 失敗\n```\n{log[-900:]}\n```")
            if not ok:
                errors.append(f"YouTube投稿処理:\n{log}")

        if tiktok_manual:
            out.append(f"🖐 **TikTokは手動で投稿してください**（目安 {tk_when}）\n"
                       f"　動画: `{runner.tiktok_mp4(name)}`\n"
                       f"　投稿文は**次のメッセージをそのままコピー**して貼り付け")

        if where in ("tiktok", "both") and auto:
            out.append(f"⚠️ TikTokは予約日時がズレることがあるので、押す前に"
                       f"**{tk_when.replace('翌朝', '明日 ').replace('夜', '今夜 ')}**"
                       f"になっているか目視確認してね")
        if needs_hand:
            out.append("※ Macの前に居られない時は `おまかせ投稿` と送れば確定まで自動で押します")
        await send(ch, "\n".join(out))

        # 投稿文は**単独のメッセージ**でコードブロックに入れる。まわりの説明が
        # 一緒にコピーされないようにするため（コピーボタン／長押しでそのまま取れる）。
        if tiktok_manual:
            cap = runner.tiktok_caption(name)
            if cap:
                await ch.send(f"```\n{cap}\n```")
                # 5_分析 はこの行のキャプションで実測と回を突合する。手動投稿でも
                # 残しておかないと、新しい回が 実績.tsv に載らなくなる。
                await asyncio.to_thread(runner.log_manual_tiktok, name, cap)
            else:
                await ch.send("⚠️ TikTok用の投稿文が見つかりません: "
                              f"`{runner.caption_txt(name, 'tiktok')}`")

        if errors:
            await ask_codex(ch, f"{name} の投稿準備（{where}）", "\n\n".join(errors))


async def do_post(ch: discord.abc.Messageable, name: str, where: str, slot: str,
                  auto: bool = False) -> None:
    """投稿処理の想定外例外もCodexへ渡す公開入口。"""
    try:
        await _do_post_inner(ch, name, where, slot, auto)
    except Exception:  # noqa: BLE001
        detail = traceback.format_exc()
        await send(ch, f"❌ 投稿処理で想定外のエラー\n```\n{detail[-1200:]}\n```")
        await ask_codex(ch, f"{name} の投稿処理（{where}）の想定外エラー", detail)


# --- 週次レビュー -----------------------------------------------------------
async def do_weekly(ch: discord.abc.Messageable, headline: str = "") -> None:
    """先週の実測を見て、リサーチの検索語と台本の重点方針を更新する。

    やるのは Check→Act のうち**機械が決めてよいところだけ**:
      ・5_分析/実績.tsv を取り直す（＝勝ち筋/沈んだ角度の判定が最新になる）
      ・1_リサーチ/検索語.json … 次のリサーチが探す語
      ・2_台本生成/重点方針.md … 次の台本プロンプトに入る方針
    ネタ提案は下書きのまま置くだけで、採用/却下は社長（メモリ act-step-human-gate）。
    **投稿には一切触らない**ので、走っても勝手に何かが世に出ることはない。

    実測の取り直しで投稿用プロファイルのChromeを開くため、生成・投稿とは
    _busy で排他する（Chromeの取り合いで両方コケるのを避ける）。
    """
    if _busy.locked():
        await ch.send("⏳ いま別の処理が走っています。終わってから `分析` と言ってね")
        return
    async with _busy:
        th = await open_thread(ch, headline or "📊 週次レビューを始めます",
                               f"{datetime.now():%m/%d} 週次レビュー")
        await th.send("🔎 実測を取り直して分析します（10〜20分）。"
                      "この間 投稿用のChromeが立ち上がります")
        try:
            ok, res, log = await asyncio.to_thread(runner.weekly_review)
        except Exception:  # noqa: BLE001 - 週次が落ちても静かに死なせない
            detail = traceback.format_exc()
            await send(th, f"❌ 週次レビューで想定外のエラー\n```\n{detail[-1200:]}\n```")
            await ask_codex(th, "週次レビュー（5_分析/scripts/weekly_review.py）", detail)
            return
        if not ok:
            await send(th, f"❌ 週次レビューに失敗\n```\n{log[-1200:]}\n```")
            await ask_codex(th, "週次レビュー（5_分析/scripts/weekly_review.py）", log)
            return

        body = ["📊 **週次レビュー完了**", ""]
        body += [f"・{line}" for line in res.get("summary", [])] or ["・（要点なし）"]
        if res.get("changes"):
            body += ["", "**反映しました（次の生成から効きます）**", *res["changes"]]
        else:
            body += ["", "🔁 変更なし（先週の方針を続けます）"]
        if res.get("notes"):
            body += ["", f"📝 社長の判断待ち: {res['notes']}"]
        body += res.get("warnings", [])
        if res.get("proposal"):
            body += ["", f"💡 ネタ提案の下書き: `{Path(res['proposal']).name}`"
                         "（採用/却下は社長。使う時は `作って` の前に見てね）"]
        await send(th, "\n".join(body))

        for key in ("report", "proposal"):
            path = Path(res.get(key) or "")
            if path.exists() and path.stat().st_size < 7_000_000:
                try:
                    await th.send(file=discord.File(str(path)))
                except discord.HTTPException as e:
                    print(f"[weekly] {path.name} を送れません: {e}")


# --- 自動実行 ---------------------------------------------------------------
CATCHUP_LIMIT = 12 * 3600   # 定刻からこの時間内なら寝過ごし分を後追いで実行する


def weekly_due(w: dict, now: datetime) -> datetime | None:
    """直近の「その曜日のその時刻」を返す。設定が壊れていれば None。

    走らせるのは常に**過去いちばん近い定刻**。こうしておくと、Macが寝ていて
    日曜22:00を逃しても、月曜の朝に起きた時点で同じ回として追いかけられる
    （＝週をまたいでも二重に走らない）。
    """
    try:
        hh, mm = (int(x) for x in str(w["time"]).split(":"))
        dow = int(w["dow"]) % 7
    except (KeyError, TypeError, ValueError):
        return None
    due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    due -= timedelta(days=(now.weekday() - dow) % 7)
    return due - timedelta(days=7) if due > now else due


async def weekly_tick(now: datetime) -> bool:
    """週次レビューの定刻を過ぎていたら走らせる。走ったら True。"""
    w = _weekly()
    if not w["enabled"]:
        return False
    due = weekly_due(w, now)
    if due is None:
        return False
    late = (now - due).total_seconds()
    if late > CATCHUP_LIMIT or w["last_fired"] == due.strftime("%Y-%m-%d"):
        return False
    ch = _channel()
    if not ch:
        print("[weekly] 通知先チャンネル未登録のため発火スキップ")
        return False
    _save_weekly(last_fired=due.strftime("%Y-%m-%d"))
    delay = "" if late < 120 else f"（定刻{w['time']}に寝てたので今から）"
    await do_weekly(ch, headline=(
        f"📊 **毎週{DOW_JA[int(w['dow']) % 7]}曜 {w['time']} 週次レビュー**{delay}"
        " — 先週の実測を見て、リサーチと台本の方針を更新します"))
    return True


@tasks.loop(seconds=30)
async def clock() -> None:
    now = datetime.now()
    # 週次が先。定刻がぶつかった時は分析を優先する（生成は翌朝また走る）
    if await weekly_tick(now):
        return
    a = _auto()
    if not a["enabled"]:
        return
    if a["last_fired"] == now.strftime("%Y-%m-%d"):
        return
    try:
        hh, mm = (int(x) for x in a["time"].split(":"))
    except ValueError:
        return
    due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    late = (now - due).total_seconds()
    # Macがスリープしていて定刻を寝過ごすことがあるので、当日中なら起きた時点で追いかけて実行する
    if late < 0 or late > CATCHUP_LIMIT:
        return
    ch = _channel()
    if not ch:
        print("[auto] 通知先チャンネル未登録のため発火スキップ")
        return
    _save_auto(last_fired=now.strftime("%Y-%m-%d"))
    # 見出しはチャンネルに残り、そこにスレッドがぶら下がる（毎朝1行ずつ積まれる）
    delay = "" if late < 120 else f"（定刻{a['time']}に寝てたので今から）"
    await do_make(ch, "auto",
                  headline=f"🤖 **{a['time']} 自動実行**{delay} — 今日の1本を作ります")


@clock.before_loop
async def _wait() -> None:
    await bot.wait_until_ready()


@clock.error
async def _clock_error(error: BaseException) -> None:
    """定時実行ループ自体の例外も通知し、Codexへ渡す。"""
    detail = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    ch = _channel()
    if ch:
        try:
            await send(ch, f"❌ 定時実行で想定外のエラー\n```\n{detail[-1200:]}\n```")
        except discord.HTTPException as e:
            print(f"[clock] Discordへエラーを送れません: {e}")
            ch = None
    print(f"[clock] {detail}")
    await ask_codex(ch, "Discord Botの定時実行", detail)


# --- イベント ---------------------------------------------------------------
@bot.event
async def on_error(event_method: str, *args, **kwargs) -> None:
    """メッセージ処理など、個別処理をすり抜けた例外の最終救援口。"""
    detail = traceback.format_exc()
    ch = getattr(args[0], "channel", None) if args else None
    if ch:
        try:
            await send(ch, f"❌ {event_method} で想定外のエラー\n```\n{detail[-1200:]}\n```")
        except discord.HTTPException as e:
            print(f"[on_error] Discordへエラーを送れません: {e}")
            ch = None
    print(f"[on_error/{event_method}] {detail}")
    await ask_codex(ch, f"Discord Botイベント: {event_method}", detail)


@bot.event
async def on_ready() -> None:
    print(f"✔ ログイン: {bot.user}")
    print(auto_status().splitlines()[0])
    print(weekly_status().splitlines()[0])
    await learn_owner()
    if not clock.is_running():
        clock.start()


@bot.event
async def on_message(msg: discord.Message) -> None:
    if msg.author.bot:
        return
    # スレッド内の発言も受ける（判定は常に親チャンネルで行う）
    home = _home_id(msg.channel)
    if config.CHANNEL_ID and home != config.CHANNEL_ID:
        return
    if config.ALLOWED_USER_ID and msg.author.id != config.ALLOWED_USER_ID:
        return
    # 最初に話しかけられた場所と相手を覚える。場所は自動実行の通知先、
    # 相手は動画が出来た時のメンション先（.env 未設定でも名指しできるように）。
    # スレッドのIDを覚えてしまうと毎朝の自動実行が古いスレッドに埋もれるので親を覚える
    s = runner.load_state()
    changed = False
    if not config.CHANNEL_ID and s.get("channel_id") != home:
        s["channel_id"], changed = home, True
    if not config.MENTION_USER_ID and s.get("owner_id") != msg.author.id:
        s["owner_id"], changed = msg.author.id, True
    if changed:
        runner.save_state(s)

    text = msg.content.strip()
    if not text:
        return
    ch = msg.channel

    post_kw = any(k in text for k in ("投稿", "アップ", "出す", "出して"))
    # 「おまかせ投稿」「自動で投稿」= TikTokの確定ボタンまで自動で押す
    auto_press = any(k in text for k in ("おまかせ", "お任せ", "自動", "全部", "代わり"))

    # 週次レビューの設定（「毎週日曜22時」「週次オフ」「週次」）。
    # 日次の分岐より前に見る。あとに置くと「毎週…22時」が日次の時刻設定に食われる。
    if any(k in text for k in ("週次", "毎週")) and not post_kw:
        if any(k in text for k in ("オフ", "停止", "止め", "切")):
            _save_weekly(enabled=False)
            await ch.send("🛑 週次レビューをオフにしました")
            return
        fields = {}
        dow = re.search(r"([月火水木金土日])\s*曜", text)
        if dow:
            fields["dow"] = DOW_JA.index(dow.group(1))
        at = re.search(r"(\d{1,2})\s*[:時]\s*(\d{1,2})?", text)
        if at:
            fields["time"] = f"{int(at.group(1)):02d}:{int(at.group(2) or 0):02d}"
        if fields or any(k in text for k in ("オン", "開始", "スタート")):
            _save_weekly(enabled=True, **fields)
            await ch.send("🟢 設定しました\n" + weekly_status())
            return
        await ch.send(weekly_status())
        return

    # 週次レビューを今すぐ回す（定刻を待たずに分析したい時）
    if any(k in text for k in ("分析", "レビュー")) and not post_kw:
        await do_weekly(ch, headline="📊 **週次レビュー**（手動）"
                                     " — 実測を見て、リサーチと台本の方針を更新します")
        return

    # 自動実行（時計）の設定。「自動で投稿」を時計設定と取り違えないよう投稿系は除く
    if ("自動" in text or "毎日" in text) and not post_kw:
        if any(k in text for k in ("オフ", "停止", "止め", "切")):
            _save_auto(enabled=False)
            await ch.send("🛑 自動実行をオフにしました")
            return
        m = re.search(r"(\d{1,2})\s*[:時]\s*(\d{1,2})?", text)
        if m:
            _save_auto(enabled=True, time=f"{int(m.group(1)):02d}:{int(m.group(2) or 0):02d}")
            await ch.send("🟢 設定しました\n" + auto_status())
            return
        if any(k in text for k in ("オン", "開始", "スタート")):
            _save_auto(enabled=True)
            await ch.send("🟢 自動実行をオンにしました\n" + auto_status())
            return
        await ch.send(auto_status())
        return

    if any(k in text for k in ("ヘルプ", "help", "使い方")):
        await ch.send(HELP)
        return
    if "状態" in text:
        await ch.send(auto_status() + "\n" + weekly_status())
        return

    url = re.search(r"https?://\S*tiktok\.com/\S+", text)
    # 回の番号は2〜4桁。「3つ目」「2つ」「30日」のような助数詞付きは回番号ではないので外す
    # （「036 直して 3つ目の項目を消して」で 3 を回番号と誤読しないため）
    num = re.search(r"(?<!\d)(\d{2,4})(?![\d\s]*[つ個番回本年月日時分秒歳割万円%％:：])", text)
    name = f"本番_{int(num.group(1)):03d}" if num else None
    slot = "pm" if any(k in text for k in ("夜", "18:30", "夕方")) else "am"

    if url:
        await do_make(ch, "url", url.group(0),
                      headline="🔗 参考動画を受け取りました。台本化から始めます")
        return

    # 台本の手直し（「直して タイトルを短く」「036 修正 3つ目いらん」）
    if any(k in text for k in ("直して", "修正", "変えて", "作り直")):
        target = name if (name and runner.script_txt(name).exists()) else runner.latest_ready()
        if not target:
            await ch.send("⚠️ 直せる台本がまだありません")
            return
        # 命令語と回の番号だけを落とした残りが「どう直すか」。
        # 指示文の中の数字（「3つ目」等）は消さないよう、落とすのは回番号1個と命令語1個だけ。
        instruction = text
        if num:
            instruction = instruction.replace(num.group(1), "", 1)
        instruction = re.sub(r"(直して|修正して|修正|変えて|作り直して|作り直し)",
                             "", instruction, count=1).strip("　 、。,.")
        if len(instruction) < 2:
            await ch.send("どう直すか一緒に書いてね。例:\n"
                          "`直して タイトルをもっと短く`\n"
                          "`036 直して 3つ目の項目を消して2つにして`")
            return
        await do_make(ch, "revise", f"{target}\n{instruction}",
                      headline=f"🔧 **{target}** を手直しします")
        return

    # 完成済みの動画をもう一度見たいとき（作り直さずプレビューだけ送る）
    if any(k in text for k in ("見せて", "見たい", "プレビュー", "確認")) and not post_kw:
        target = name if (name and runner.tiktok_mp4(name).exists()) else runner.latest_ready()
        if not target:
            await ch.send("⚠️ まだ完成した動画がありません")
            return
        await send_preview(await route(ch, target), target, f"🎬 **{target}**")
        return

    if post_kw:
        # 「18:30に投稿」のような時刻表記を回の番号と誤読しないよう、実在する回だけ採用する
        target = name if (name and runner.tiktok_mp4(name).exists()) else runner.latest_ready()
        if not target:
            await ch.send("⚠️ 投稿できる動画がまだありません")
            return
        dest = await route(ch, target)
        if auto_press:
            head = (f"📤 **{target}** を TikTok と YouTube に予約します"
                    f"（{runner.slot_label(slot)}・確定まで自動で押します）")
        else:
            head = (f"📤 **{target}** — YouTubeを予約し"
                    f"（{runner.slot_label(slot, 'youtube')}）、"
                    "TikTokは**貼る投稿文を出します**（手動投稿）")
        await dest.send(head)
        await do_post(dest, target, "both", slot, auto_press)
        return

    if any(k in text for k in ("作", "生成", "つく")):
        if name and runner.script_txt(name).exists():
            await do_make(ch, "name", name, headline=f"🎬 **{name}** の台本から作ります")
        else:
            await do_make(ch, "auto",
                          headline="🤖 リサーチから全自動で1本作ります（10〜20分かかります）")
        return

    await ch.send(HELP)


def main() -> None:
    lack = config.missing_secrets()
    if lack:
        raise SystemExit(f"❌ .env に {', '.join(lack)} が未設定です。README を参照。")
    bot.run(config.BOT_TOKEN)


if __name__ == "__main__":
    main()
