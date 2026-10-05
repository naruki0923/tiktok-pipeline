"""youtube_upload.py の二重アップロード防止（issue #24）。外部送信はしない。"""
from __future__ import annotations

import argparse
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import youtube_upload

HEADER = ["datetime", "video", "status", "video_id", "url", "scheduled_for", "title"]


class UploadedBeforeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.log = Path(self._tmp.name) / "youtube_log.csv"
        patcher = patch.object(youtube_upload, "LOG_CSV", self.log)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.video = Path(self._tmp.name) / "本番_104_YouTube.mp4"

    def write(self, *rows: list[str]) -> None:
        with self.log.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(HEADER)
            w.writerows(rows)

    def args(self, force: bool = False) -> argparse.Namespace:
        return argparse.Namespace(video=self.video, force=force)

    def test_finds_an_earlier_upload_of_the_same_video(self) -> None:
        self.write(["2026-10-04T06:00:00", "本番_104_YouTube.mp4", "uploaded",
                    "abc", "https://youtu.be/abc", "", "t"])
        self.assertEqual("https://youtu.be/abc",
                         youtube_upload.uploaded_before(self.video)["url"])

    def test_failed_attempts_do_not_count(self) -> None:
        self.write(["2026-10-04T06:00:00", "本番_104_YouTube.mp4", "upload_failed", "", "", "", "t"],
                   ["2026-10-04T06:01:00", "本番_104_YouTube.mp4", "dry_run", "", "", "", "t"],
                   ["2026-10-04T06:02:00", "本番_103_YouTube.mp4", "uploaded", "x", "u", "", "t"])
        self.assertIsNone(youtube_upload.uploaded_before(self.video))

    def test_no_log_is_fine(self) -> None:
        self.assertIsNone(youtube_upload.uploaded_before(self.video))

    def test_second_upload_stops_before_touching_youtube(self) -> None:
        self.write(["2026-10-04T06:00:00", "本番_104_YouTube.mp4", "uploaded",
                    "abc", "https://youtu.be/abc", "", "t"])
        with patch.object(youtube_upload, "load_caption") as cap, \
                patch.object(youtube_upload, "build") as api:
            self.assertEqual(0, youtube_upload.do_upload(self.args()))
        cap.assert_not_called()
        api.assert_not_called()

    def test_force_goes_ahead(self) -> None:
        self.write(["2026-10-04T06:00:00", "本番_104_YouTube.mp4", "uploaded",
                    "abc", "https://youtu.be/abc", "", "t"])
        with patch.object(youtube_upload, "load_caption",
                          side_effect=RuntimeError("reached")):
            with self.assertRaisesRegex(RuntimeError, "reached"):
                youtube_upload.do_upload(self.args(force=True))


if __name__ == "__main__":
    unittest.main()
