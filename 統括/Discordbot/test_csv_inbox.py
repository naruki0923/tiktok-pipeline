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
