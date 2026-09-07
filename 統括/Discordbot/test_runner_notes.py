"""投稿ログから「押す前に人が直す点」を拾えるかを確認する。"""
from unittest import TestCase, main

import runner


class ManualFixupsTest(TestCase):
    def test_flags_a_schedule_that_never_got_set(self) -> None:
        log = ("投稿予約を設定します: 2026-08-26 08:30\n"
               "  [予約] 日付 設定失敗（現在 2026-08-25 / 目標 2026-08-26）\n"
               "  [予約] 時刻 設定成功: 08:30\n")
        notes = runner.manual_fixups(log)

        self.assertEqual(1, len(notes))
        self.assertIn("予約日", notes[0])

    def test_flags_missing_bgm(self) -> None:
        log = "⚠️ BGM追加を確認できず。Chromeで手動確認してください（他の設定は続行）。"

        self.assertIn("BGM", runner.manual_fixups(log)[0])

    def test_flags_both_at_once(self) -> None:
        log = "[予約] 日付 設定失敗\n[BGM] 楽曲行が見つからない: {'err': 'no-title'}"

        self.assertEqual(2, len(runner.manual_fixups(log)))

    def test_quiet_on_a_clean_run(self) -> None:
        log = ("✅ キャプションを入力しました（内容確認OK）。\n"
               "  [予約] 日付 設定成功: 2026-08-26\n"
               "  [予約] 時刻 設定成功: 08:30\n")

        self.assertEqual([], runner.manual_fixups(log))


if __name__ == "__main__":
    main()


class TrimLogTest(TestCase):
    """縮めたログから序盤の警告が落ちないこと（2026-09-05のBGM見落とし対策）。"""

    def test_keeps_early_warning_when_tail_is_cut(self) -> None:
        log = ("⚠️ BGM追加を確認できず。Chromeで手動確認してください（他の設定は続行）。\n"
               + "うめ草\n" * 2000
               + "  [予約] 時刻 設定成功: 08:30\n")

        trimmed = runner._trim_log(log)

        self.assertIn("BGM追加を確認できず", trimmed)
        self.assertIn("[予約] 時刻 設定成功", trimmed)
        self.assertIn("BGM", runner.manual_fixups(trimmed)[0])

    def test_short_log_is_untouched(self) -> None:
        log = "✅ キャプションを入力しました（内容確認OK）。\n"

        self.assertEqual(log, runner._trim_log(log))
