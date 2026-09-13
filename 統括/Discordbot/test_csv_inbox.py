"""TikTok Studio の CSV を Discord から受け取って 5_分析/取込/ に置く（issue #14）。

週次レビューの実測はこのCSVだけが入口。定刻に無ければ催促し、届いたら走らせる。
"""
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main

import bot
import config
import runner

CONTENT = (b'"Time","Video title","Video link","Post time","Total likes","Total comments","Total shares","Total views"\n'
           b'"9\xe6\x9c\x8813\xe6\x97\xa5","t","https://www.tiktok.com/@x/video/7681870521679564053","9\xe6\x9c\x886\xe6\x97\xa5","1","2","3","4"\n')
OVERVIEW = b'"Date","Video Views","Profile Views","Likes","Comments","Shares"\n"7\xe6\x9c\x8814\xe6\x97\xa5","75","0","3","0","1"\n'


class CsvKindTest(TestCase):
    def test_recognises_by_filename(self) -> None:
        self.assertEqual("Content", runner.csv_kind("Content.csv", b""))
        self.assertEqual("Overview", runner.csv_kind("overview (1).csv", b""))

    def test_recognises_by_header_when_renamed(self) -> None:
        """スマホからだと名前が変わる（「ファイル.csv」等）ので中身でも見る。"""
        self.assertEqual("Content", runner.csv_kind("ファイル.csv", CONTENT))
        self.assertEqual("Overview", runner.csv_kind("data.csv", OVERVIEW))

    def test_unknown_csv_is_rejected(self) -> None:
        self.assertIsNone(runner.csv_kind("memo.csv", b"a,b\n1,2\n"))


class InboxTest(TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self._orig = config.INBOX
        config.INBOX = Path(self._tmp.name) / "取込"

    def tearDown(self) -> None:
        config.INBOX = self._orig
        self._tmp.cleanup()

    def test_saves_with_timestamp_and_counts_rows(self) -> None:
        p = runner.save_csv("Content", CONTENT, datetime(2026, 9, 13, 21, 5, 7))

        self.assertEqual("Content_20260913_210507.csv", p.name)
        self.assertEqual(CONTENT, p.read_bytes())
        self.assertEqual(1, runner.csv_rows(p))

    def test_two_files_in_the_same_second_do_not_overwrite_each_other(self) -> None:
        """7日版と60日版を1メッセージに2つ付けると同じ秒に来る。"""
        when = datetime(2026, 9, 13, 21, 5, 7)
        a = runner.save_csv("Content", CONTENT, when)
        b = runner.save_csv("Content", OVERVIEW, when)

        self.assertNotEqual(a, b)
        self.assertEqual("Content_20260913_210507_2.csv", b.name)
        self.assertEqual(CONTENT, a.read_bytes())

    def test_csv_since_only_sees_content_files_newer_than_the_cutoff(self) -> None:
        import os
        old = runner.save_csv("Content", CONTENT, datetime(2026, 9, 6, 21, 0))
        os.utime(old, (0, 0))
        runner.save_csv("Overview", OVERVIEW)
        self.assertIsNone(runner.csv_since(datetime.now() - timedelta(days=7)))

        new = runner.save_csv("Content", CONTENT)
        self.assertEqual(new, runner.csv_since(datetime.now() - timedelta(days=7)))

    def test_missing_inbox_dir_is_none(self) -> None:
        self.assertIsNone(runner.csv_since(datetime(2000, 1, 1)))


class NudgeTest(TestCase):
    def test_nudge_tells_where_to_get_the_csv(self) -> None:
        text = bot.csv_nudge({"time": "22:00"}, datetime(2026, 9, 13, 22, 0))

        self.assertIn("Content.csv", text)
        self.assertIn("データをダウンロード", text)
        self.assertIn("09/13 22:00", text)


if __name__ == "__main__":
    main()


# --- 定刻 → 催促 → 届いたら走る（Discordには繋がない） ------------------------
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


class _Att:
    def __init__(self, filename: str, data: bytes) -> None:
        self.filename, self._data = filename, data

    async def read(self) -> bytes:
        return self._data


class NudgeThenRunTest(TestCase):
    """日曜22:00にCSVが無い → 催促だけ。CSVが投げられた瞬間に週次レビューが走る。"""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self._orig_inbox = config.INBOX
        config.INBOX = Path(self._tmp.name) / "取込"
        self.state = {"weekly": {"enabled": True, "dow": 6, "time": "22:00", "last_fired": ""}}
        self.ch = MagicMock()
        self.ch.send = AsyncMock()
        self.patches = [
            patch.object(runner, "load_state", side_effect=lambda: self.state),
            patch.object(runner, "save_state", side_effect=lambda s: self.state.update(s)),
            patch.object(bot, "_channel", return_value=self.ch),
            patch.object(bot, "_analysis_channel", return_value=self.ch),
            patch.object(bot, "do_weekly", new=AsyncMock()),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self) -> None:
        for p in self.patches:
            p.stop()
        config.INBOX = self._orig_inbox
        self._tmp.cleanup()

    def _msg(self, *atts: _Att) -> MagicMock:
        m = MagicMock()
        m.attachments = list(atts)
        m.channel = self.ch
        return m

    def test_no_csv_at_2200_nudges_instead_of_running(self) -> None:
        fired = asyncio.run(bot.weekly_tick(datetime(2026, 9, 13, 22, 0, 30)))

        self.assertTrue(fired)
        bot.do_weekly.assert_not_awaited()
        self.assertEqual("2026-09-13", self.state["weekly"]["waiting_csv"])
        self.assertIn("まだ届いていません", self.ch.send.await_args.args[0])

    def test_csv_arriving_while_waiting_runs_the_review(self) -> None:
        asyncio.run(bot.weekly_tick(datetime(2026, 9, 13, 22, 0, 30)))

        took = asyncio.run(bot.take_csv(self._msg(_Att("Content.csv", CONTENT))))

        self.assertTrue(took)
        self.assertEqual(1, len(list(config.INBOX.glob("Content_*.csv"))))
        bot.do_weekly.assert_awaited_once()
        self.assertEqual("", self.state["weekly"]["waiting_csv"])
        self.assertIn("受け取りました", self.ch.send.await_args_list[-1].args[0])

    def test_csv_sent_before_2200_is_kept_and_the_tick_runs_normally(self) -> None:
        """20〜22時に先に投げてあれば、定刻に催促せずそのまま走る。"""
        asyncio.run(bot.take_csv(self._msg(_Att("Content.csv", CONTENT))))
        bot.do_weekly.assert_not_awaited()          # 定刻前は保存だけ

        fired = asyncio.run(bot.weekly_tick(datetime(2026, 9, 13, 22, 0, 30)))

        self.assertTrue(fired)
        bot.do_weekly.assert_awaited_once()
        self.assertFalse(self.state["weekly"].get("waiting_csv"))

    def test_overview_alone_does_not_trigger_the_review(self) -> None:
        asyncio.run(bot.weekly_tick(datetime(2026, 9, 13, 22, 0, 30)))

        asyncio.run(bot.take_csv(self._msg(_Att("Overview.csv", OVERVIEW))))

        bot.do_weekly.assert_not_awaited()
        self.assertEqual("2026-09-13", self.state["weekly"]["waiting_csv"])

    def test_non_csv_attachment_is_ignored(self) -> None:
        took = asyncio.run(bot.take_csv(self._msg(_Att("photo.png", b"\x89PNG"))))

        self.assertFalse(took)
        self.ch.send.assert_not_awaited()

    def test_last_weeks_csv_does_not_count_for_this_week(self) -> None:
        import os
        old = runner.save_csv("Content", CONTENT)
        os.utime(old, (datetime(2026, 9, 5).timestamp(),) * 2)   # 先週土曜に届いた分

        fired = asyncio.run(bot.weekly_tick(datetime(2026, 9, 13, 22, 0, 30)))

        self.assertTrue(fired)
        bot.do_weekly.assert_not_awaited()
        self.assertEqual("2026-09-13", self.state["weekly"]["waiting_csv"])
