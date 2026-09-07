"""外部通信を行わず、音声取得のタイムアウト処理を確認する。"""
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch

import transcribe


class FetchAudioYtdlpTest(TestCase):
    def test_each_attempt_has_timeout_and_exhaustion_is_reported(self):
        with (tempfile.TemporaryDirectory() as tmp,
              patch.object(transcribe.subprocess, "run",
                           side_effect=subprocess.TimeoutExpired(["yt-dlp"], 180)) as run,
              patch.object(transcribe.time, "sleep")):
            with self.assertRaises(SystemExit):
                transcribe._fetch_audio_ytdlp(
                    "https://www.tiktok.com/@test/video/1", tmp,
                    Path(tmp) / "audio.mp3")

        self.assertEqual(run.call_count, 6)
        self.assertTrue(all(
            call.kwargs["timeout"] == transcribe.YTDLP_ATTEMPT_TIMEOUT
            for call in run.call_args_list))

    def test_invalid_browser_media_falls_back_to_ytdlp(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            video = workdir / "video.mp4"
            video.write_bytes(b"invalid")
            expected = workdir / "fallback.mp3"
            with (patch.object(transcribe, "fetch_via_browser", return_value=video),
                  patch.object(transcribe.subprocess, "run",
                               side_effect=subprocess.CalledProcessError(1, ["ffmpeg"])),
                  patch.object(transcribe, "_fetch_audio_ytdlp", return_value=expected) as fallback):
                actual = transcribe.fetch_audio("https://example.test/video", tmp)

            self.assertEqual(actual, expected)
            fallback.assert_called_once()


if __name__ == "__main__":
    main()
