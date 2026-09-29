#!/usr/bin/env python3
"""auto_research の時間切れ対策を外部通信なしで確認する。"""
import subprocess
import unittest
from unittest.mock import Mock, patch

import auto_research


class ProbeTest(unittest.TestCase):
    def test_yt_dlp_fallback_times_out_once_and_returns_none(self):
        with patch.object(
            auto_research.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(["yt-dlp"], 45),
        ) as run:
            self.assertIsNone(auto_research.probe("https://example.invalid/video/1"))

        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.kwargs["timeout"],
                         auto_research.YT_DLP_FALLBACK_TIMEOUT)

    def test_navigation_race_retries_evaluate_without_yt_dlp(self):
        page = Mock()
        page.evaluate.side_effect = [
            RuntimeError("Execution context was destroyed, most likely because of a navigation"),
            {
                "views": 62100,
                "duration": 87,
                "create_time": None,
                "likes": 100,
                "comments": 5,
                "author": "test",
                "followers": 1000,
                "title": "title",
            },
        ]

        result = auto_research.probe_page(page, "https://example.invalid/video/1")

        self.assertEqual(result["views"], 62100)
        self.assertEqual(page.evaluate.call_count, 2)
        page.goto.assert_called_once_with(
            "https://example.invalid/video/1",
            timeout=45_000,
            wait_until="domcontentloaded",
        )



class SearchKeywordsTest(unittest.TestCase):
    """65歳系の語は商材の対象外。検索語.json に残っていても使わない。"""

    def test_drops_65_words_from_the_json(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            kw = Path(d) / "検索語.json"
            kw.write_text(json.dumps({"keywords": ["退職給付金", "65歳 退職 失業保険",
                                                   "60歳 給付金 申請"]}, ensure_ascii=False),
                          encoding="utf-8")
            with patch.object(auto_research, "KEYWORDS_JSON", kw):
                self.assertEqual(["退職給付金", "60歳 給付金 申請"],
                                 auto_research.search_keywords())

    def test_defaults_have_no_65_words(self) -> None:
        self.assertFalse([k for k in auto_research.DEFAULT_KEYWORDS
                          if auto_research.off_target(k)])


if __name__ == "__main__":
    unittest.main()
