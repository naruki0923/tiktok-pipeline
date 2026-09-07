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


if __name__ == "__main__":
    unittest.main()
