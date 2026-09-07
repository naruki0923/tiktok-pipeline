"""editor_ops.py の外部操作を伴わない回帰テスト。"""
from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import editor_ops


class PickerOverlayTest(unittest.TestCase):
    def test_failed_date_selection_closes_overlay(self) -> None:
        """日付を選べなくてもカレンダーoverlayを開いたままにしない。"""
        page = Mock()
        field = Mock()
        field.input_value.return_value = "2026-08-27"

        with (
            patch.object(editor_ops, "_find_date_field", return_value=field),
            patch.object(editor_ops, "_click_calendar_day", return_value=False),
            patch.object(editor_ops, "_click_next_month"),
            patch.object(editor_ops.time, "sleep"),
        ):
            self.assertFalse(editor_ops.set_date(page, "2026-08-28"))

        field.click.assert_called_once_with(timeout=5000)
        page.keyboard.press.assert_called_with("Escape")

    def test_blocked_time_field_returns_false_without_typing(self) -> None:
        """overlayに遮られた時刻欄を強制クリックせず安全に中断する。"""
        page = Mock()
        field = Mock()
        field.input_value.return_value = "06:00"
        field.click.side_effect = RuntimeError("overlay intercepts pointer events")
        page.query_selector_all.return_value = [field]

        with patch.object(editor_ops.time, "sleep"):
            self.assertFalse(editor_ops._set_time_picker(page, "08:30"))

        field.click.assert_called_once_with(timeout=5000)
        page.keyboard.press.assert_called_once_with("Escape")
        page.keyboard.type.assert_not_called()


class BgmOverlayTest(unittest.TestCase):
    def test_blocked_volume_field_returns_false_without_typing(self) -> None:
        """別モーダルの背面にある音量欄を操作しない。"""
        page = Mock()
        handle = Mock()
        field = Mock()
        field.click.side_effect = RuntimeError("overlay intercepts pointer events")
        handle.as_element.return_value = field
        page.evaluate_handle.return_value = handle

        self.assertFalse(editor_ops.set_bgm_volume(page, -20))

        field.click.assert_called_once_with(timeout=5000)
        page.keyboard.type.assert_not_called()


class BgmAliasTest(unittest.TestCase):
    """曲名の表示が英訳名/原題のどちらでも楽曲行を見つけられること。"""

    def test_aliases_include_japanese_title(self) -> None:
        names = editor_ops.bgm_aliases(editor_ops.DEFAULT_BGM_NAME)
        self.assertEqual(names[0], editor_ops.DEFAULT_BGM_NAME)
        self.assertIn("ジブリっぽいピアノソロのバラード", names)

    def test_unknown_name_is_used_as_is(self) -> None:
        self.assertEqual(editor_ops.bgm_aliases("Some Other Song"), ["Some Other Song"])

    def test_add_bgm_searches_every_alias(self) -> None:
        """2026-08-20以降、原題表示になった楽曲行を英語名だけで探して取り逃していた。"""
        page = Mock()
        page.evaluate.return_value = {"x": 10, "y": 20}

        with (
            patch.object(editor_ops, "_click_text", return_value=True),
            patch.object(editor_ops, "_bgm_on_timeline", return_value=True) as on_timeline,
            patch.object(editor_ops.time, "sleep"),
        ):
            self.assertTrue(editor_ops.add_bgm(page))

        passed = page.evaluate.call_args[0][1]
        self.assertIn("ジブリっぽいピアノソロのバラード", passed)
        self.assertIn("ジブリっぽいピアノソロのバラード", on_timeline.call_args[0][1])

    def test_failure_reports_visible_track_titles(self) -> None:
        """見つからない時は、実際に何が表示されていたかをログに残す。"""
        page = Mock()
        page.evaluate.return_value = {"err": "no-title"}

        with (
            patch.object(editor_ops, "_click_text", return_value=True),
            patch.object(editor_ops, "_favorite_track_titles",
                         return_value=["別の曲"]) as titles,
            patch.object(editor_ops.time, "sleep"),
        ):
            self.assertFalse(editor_ops.add_bgm(page))

        titles.assert_called_once_with(page)


if __name__ == "__main__":
    unittest.main()
