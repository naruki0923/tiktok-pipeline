"""週次レビューの定刻判定。毎週日曜22:00に1回だけ走ること。"""
from datetime import datetime
from unittest import TestCase, main

import bot
import runner

SUNDAY_22 = {"enabled": True, "dow": 6, "time": "22:00", "last_fired": ""}


class WeeklyDueTest(TestCase):
    def test_fires_at_the_scheduled_minute(self) -> None:
        now = datetime(2026, 9, 6, 22, 0, 30)          # 日曜 22:00:30

        due = bot.weekly_due(SUNDAY_22, now)

        self.assertEqual(datetime(2026, 9, 6, 22, 0), due)
        self.assertLess((now - due).total_seconds(), bot.CATCHUP_LIMIT)

    def test_catches_up_after_the_mac_slept(self) -> None:
        """日曜の夜に寝ていても、月曜の朝に起きた時点で同じ回として追いかける。"""
        now = datetime(2026, 9, 7, 3, 0)               # 月曜 3:00

        due = bot.weekly_due(SUNDAY_22, now)

        self.assertEqual(datetime(2026, 9, 6, 22, 0), due)
        self.assertLess((now - due).total_seconds(), bot.CATCHUP_LIMIT)

    def test_does_not_fire_before_the_time_on_the_day(self) -> None:
        """日曜の昼は「先週の回」を指すので、12時間の追いかけ枠から外れる。"""
        now = datetime(2026, 9, 6, 12, 0)              # 日曜 12:00

        due = bot.weekly_due(SUNDAY_22, now)

        self.assertEqual(datetime(2026, 8, 30, 22, 0), due)
        self.assertGreater((now - due).total_seconds(), bot.CATCHUP_LIMIT)

    def test_stays_on_the_same_run_all_week(self) -> None:
        """週の途中はずっと同じ定刻を指す（last_fired と突き合わせて二重に走らない）。"""
        due = bot.weekly_due(SUNDAY_22, datetime(2026, 9, 9, 18, 0))   # 水曜

        self.assertEqual(datetime(2026, 9, 6, 22, 0), due)
        self.assertEqual("2026-09-06", due.strftime("%Y-%m-%d"))

    def test_other_weekdays(self) -> None:
        cfg = {"enabled": True, "dow": 0, "time": "07:30", "last_fired": ""}   # 月曜

        due = bot.weekly_due(cfg, datetime(2026, 9, 9, 18, 0))          # 水曜

        self.assertEqual(datetime(2026, 9, 7, 7, 30), due)

    def test_broken_setting_is_ignored(self) -> None:
        for broken in ({"time": "あさ", "dow": 6}, {"time": "22:00"}, {"dow": None, "time": "22:00"}):
            self.assertIsNone(bot.weekly_due(broken, datetime(2026, 9, 6, 23, 0)))




class LastJsonTest(TestCase):
    """週次レビューの結果は標準出力の最終行のJSON。進捗ログに埋もれても拾えること。"""

    def test_picks_the_last_json_line(self) -> None:
        out = ('■ 材料を集めています…\n'
               '{"ok": false, "reason": "途中経過"}\n'
               '{"ok": true, "summary": ["65歳トリガーが継続して強い"]}\n')

        res = runner._last_json(out)

        self.assertTrue(res["ok"])
        self.assertEqual(["65歳トリガーが継続して強い"], res["summary"])

    def test_survives_a_broken_line(self) -> None:
        self.assertEqual({}, runner._last_json("{壊れたJSON}\nおしまい"))
        self.assertEqual({}, runner._last_json(""))


if __name__ == "__main__":
    main()
