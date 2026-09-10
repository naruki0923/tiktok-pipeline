"""エラー後の自動作り直し（do_make のループ）を、Discordにも工程にも触らずに確認する。"""
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, main
from unittest.mock import AsyncMock, patch

import bot
import config


class FakeThread:
    """送られたメッセージを溜めるだけのスレッド代わり。"""

    def __init__(self):
        self.msgs: list[str] = []

    async def send(self, text="", **_kw):
        self.msgs.append(text)

    def text(self) -> str:
        return "\n".join(self.msgs)


class DoMakeRetryTest(IsolatedAsyncioTestCase):
    def setUp(self):
        self.th = FakeThread()
        self.stack = [
            patch.object(bot, "work_thread", AsyncMock(return_value=self.th)),
            patch.object(bot, "make_logger", lambda *_a: (lambda _m: None)),
            patch.object(bot, "bind_thread", AsyncMock()),
            patch.object(bot, "send_preview", AsyncMock()),
            patch.object(bot, "send", AsyncMock()),
            patch.object(bot.asyncio, "sleep", AsyncMock()),
            patch.object(config, "AUTO_RETRY_MAX", 2),
            # 外付けSSDの有無でテスト結果が変わらないように、既定は「読める」で固定
            patch.object(bot.runner, "bg_dir_problem", return_value=None),
        ]
        for p in self.stack:
            p.start()
            self.addCleanup(p.stop)

    async def test_success_does_not_call_codex(self):
        ok = ({"name": "本番_040", "title": "t"}, "", "")
        with (patch.object(bot, "make_once", AsyncMock(return_value=ok)) as once,
              patch.object(bot, "ask_codex", AsyncMock()) as codex):
            await bot.do_make(self.th, "auto")
        self.assertEqual(once.await_count, 1)
        codex.assert_not_awaited()

    async def test_retries_after_codex_fixed_the_code(self):
        results = [(None, "動画作成（auto）", "候補ゼロ"),
                   ({"name": "本番_041", "title": "t"}, "", "")]
        with (patch.object(bot, "make_once", AsyncMock(side_effect=results)) as once,
              patch.object(bot, "ask_codex",
                           AsyncMock(return_value=(True, ["/x/pipeline.py"]))) as codex,
              patch.object(bot, "reload_code", return_value="") as reload,
              patch.object(bot, "bind_thread", AsyncMock()) as bind):
            await bot.do_make(self.th, "auto")
        self.assertEqual(once.await_count, 2)
        self.assertEqual(codex.await_count, 1)
        reload.assert_called_once()                     # 直したコードを読み直してから再実行
        bind.assert_awaited_once()                      # 2回目で完成している
        self.assertIn("🔁 自動で作り直します（2回目/最大3回）", self.th.text())
        bot.asyncio.sleep.assert_not_awaited()          # 直った時は待たない

    async def test_gives_up_after_the_cap(self):
        fail = (None, "動画作成（auto）", "候補ゼロ")
        with (patch.object(bot, "make_once", AsyncMock(return_value=fail)) as once,
              patch.object(bot, "ask_codex", AsyncMock(return_value=(False, []))),
              patch.object(bot, "send_preview", AsyncMock()) as preview):
            await bot.do_make(self.th, "auto")
        self.assertEqual(once.await_count, config.AUTO_RETRY_MAX + 1)
        preview.assert_not_awaited()
        self.assertIn("🛑 3回試したのでいったん止めます", self.th.text())
        # コードが直っていない＝TikTok側の一時制限などなので、間を空けてから試す
        self.assertEqual(bot.asyncio.sleep.await_count, config.AUTO_RETRY_MAX)

    async def test_does_not_retry_when_a_human_must_act(self):
        """台本が無い等（文脈が空）は作り直しても同じなので1回で止める。"""
        with (patch.object(bot, "make_once", AsyncMock(return_value=(None, "", ""))) as once,
              patch.object(bot, "ask_codex", AsyncMock()) as codex):
            await bot.do_make(self.th, "name", "本番_999")
        self.assertEqual(once.await_count, 1)
        codex.assert_not_awaited()


    async def test_stops_before_using_a_script_when_the_ssd_is_unreadable(self):
        """背景素材が読めない時は、リサーチも台本も使わずに1回で止める。

        2026-09-10はこれが無く、台本6本と参考動画6本を使い切ったうえで
        合成の直前に2時間ずつ固まった。人がSSDを挿す／許可を押すまで
        何度作り直しても同じなので、作り直しもしない。
        """
        with (patch.object(bot.runner, "bg_dir_problem",
                           return_value="🚫 背景素材（外付けSSD）が使えません"),
              patch.object(bot, "make_once", AsyncMock()) as once,
              patch.object(bot, "ask_codex", AsyncMock()) as codex):
            await bot.do_make(self.th, "auto")
        once.assert_not_awaited()
        codex.assert_not_awaited()
        bot.send.assert_awaited_once()
        self.assertIn("背景素材", bot.send.await_args.args[1])


class ReloadCodeTest(IsolatedAsyncioTestCase):
    async def test_ignores_files_outside_the_bot(self):
        """工程スクリプトは subprocess で毎回新しく動くので読み直し不要。"""
        with patch.object(bot.importlib, "reload") as reload:
            self.assertEqual(bot.reload_code(["/どこか/auto_research.py"]), "")
        reload.assert_not_called()

    async def test_warns_when_bot_itself_changed(self):
        with patch.object(bot.importlib, "reload"):
            note = bot.reload_code([str(config.BOT_DIR / "bot.py")])
        self.assertIn("再起動", note)

    async def test_survives_a_broken_reload(self):
        with patch.object(bot.importlib, "reload", side_effect=SyntaxError("bad")):
            note = bot.reload_code([str(config.BOT_DIR / "pipeline.py")])
        self.assertIn("読み直せませんでした", note)


class CodeSnapshotTest(IsolatedAsyncioTestCase):
    async def test_sees_the_pipeline_and_skips_venv(self):
        snap = bot.code_snapshot()
        self.assertIn(str(config.BOT_DIR / "pipeline.py"), snap)
        self.assertIn(str(config.AUTO_RESEARCH), snap)
        self.assertFalse([p for p in snap if ".venv" in Path(p).parts])


if __name__ == "__main__":
    main()
