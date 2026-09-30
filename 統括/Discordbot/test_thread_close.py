"""`投稿` を済ませた回のスレッドは20分後に閉じる（issue #22）。

見るのは3つ。
  ・投稿がうまくいった時だけ閉じる予約が入る（失敗はログを見るために開けておく）
  ・予約は state に残り、時刻が来たら1回だけ取り出される（再起動しても忘れない）
  ・手直しが始まったら予約を取り消す（作業中に閉じない）
"""
import asyncio
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import AsyncMock, MagicMock, patch

import discord

import bot
import config
import runner

NOW = datetime(2026, 9, 30, 8, 30)


def a_thread(tid: int = 111) -> MagicMock:
    th = MagicMock(spec=discord.Thread)
    th.id = tid
    th.archived = False
    th.send = AsyncMock()
    th.edit = AsyncMock()
    return th


class ThreadCloseTest(TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        for attr, value in (("CAPTION_DIR", self.dir), ("SCRIPT_TXT_DIR", self.dir),
                            ("STATE", self.dir / "state.json")):
            old = getattr(config, attr)
            setattr(config, attr, value)
            self.addCleanup(setattr, config, attr, old)

    def post(self, ch, yt_ok: bool = True) -> None:
        with patch.object(runner, "post_youtube", return_value=(yt_ok, "log")), \
                patch.object(runner, "log_manual_tiktok"), \
                patch.object(bot, "ask_codex", AsyncMock()):
            asyncio.run(bot._do_post_inner(ch, "本番_104", "both", "am"))

    def pending(self) -> dict:
        return runner.load_state().get("thread_close", {})

    def test_successful_post_schedules_close_in_20_minutes(self) -> None:
        before = datetime.now()
        self.post(a_thread(111))

        at = datetime.fromisoformat(self.pending()["111"])
        self.assertAlmostEqual((at - before).total_seconds(), 20 * 60, delta=5)

    def test_failed_post_keeps_the_thread_open(self) -> None:
        self.post(a_thread(111), yt_ok=False)

        self.assertEqual({}, self.pending())

    def test_post_in_the_channel_schedules_nothing(self) -> None:
        """スレッドが無い古い回はチャンネルに直接書く。チャンネルは閉じない。"""
        self.post(AsyncMock(spec=discord.TextChannel))

        self.assertEqual({}, self.pending())

    def test_due_threads_are_taken_once(self) -> None:
        runner.schedule_close(111, NOW - timedelta(seconds=1))
        runner.schedule_close(222, NOW + timedelta(minutes=5))

        self.assertEqual([111], runner.take_due_closes(NOW))
        self.assertEqual([], runner.take_due_closes(NOW))
        self.assertEqual(["222"], list(self.pending()))

    def test_broken_entries_are_dropped(self) -> None:
        s = runner.load_state()
        s["thread_close"] = {"111": "壊れた値", "abc": NOW.isoformat()}
        runner.save_state(s)

        self.assertEqual([], runner.take_due_closes(NOW))
        self.assertEqual({}, self.pending())

    def test_revise_in_the_thread_cancels_the_close(self) -> None:
        th = a_thread(111)
        runner.remember("本番_104", thread_id=111)
        runner.schedule_close(111, NOW)

        with patch.object(bot, "thread_for", AsyncMock(return_value=th)):
            asyncio.run(bot.work_thread(th, "revise", "本番_104\n冒頭を短く", "🔧"))

        self.assertEqual({}, self.pending())

    def test_clock_archives_the_thread(self) -> None:
        th = a_thread(111)
        runner.schedule_close(111, NOW)

        with patch.object(bot.bot, "get_channel", return_value=th):
            asyncio.run(bot.close_due_threads(NOW))

        th.edit.assert_awaited_once_with(archived=True)
        self.assertIn("閉じます", th.send.call_args.args[0])

    def test_already_archived_thread_is_left_alone(self) -> None:
        th = a_thread(111)
        th.archived = True
        runner.schedule_close(111, NOW)

        with patch.object(bot.bot, "get_channel", return_value=th):
            asyncio.run(bot.close_due_threads(NOW))

        th.edit.assert_not_called()
        th.send.assert_not_called()

    def test_failure_does_not_stop_the_clock(self) -> None:
        th = a_thread(222)
        th.edit.side_effect = discord.HTTPException(MagicMock(status=403), "Missing Permissions")
        runner.schedule_close(222, NOW)

        with patch.object(bot.bot, "get_channel", return_value=th):
            asyncio.run(bot.close_due_threads(NOW))     # 例外が外に出ないこと

        self.assertEqual({}, self.pending())


if __name__ == "__main__":
    main()
