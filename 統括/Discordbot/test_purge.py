"""作ってから30日たった動画を消す（issue #24）。

見るのは4つ。
  ・消すのは古い重いファイル（mp4・プレビュー・wav/mp3）だけ。台本 txt・timing json は残す
  ・消したものは削除ログに残り、`投稿 040` のように消えた回を指定されたら案内して止まる
    （黙って最新の回に置き換えない）
  ・掃除は1日1回。生成・投稿の最中は待つ
  ・YouTubeを上げ済みで飛ばした時は「上げ済み」と言い、失敗扱い（Codex救援）にしない
"""
import asyncio
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import AsyncMock, patch

import discord

import bot
import config
import runner

NOW = datetime(2026, 10, 5, 6, 0)


class PurgeTest(TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.out, self.cache, self.voice = root / "output", root / "cache", root / "voice"
        for d in (self.out, self.cache, self.voice):
            d.mkdir()
        for attr, value in (("OUTPUT_DIR", self.out), ("CACHE", self.cache),
                            ("SCRIPT_TXT_DIR", self.voice), ("CAPTION_DIR", root),
                            ("PURGE_LOG", root / "削除ログ.csv"),
                            ("STATE", root / "state.json"), ("PURGE_DAYS", 30)):
            old = getattr(config, attr)
            setattr(config, attr, value)
            self.addCleanup(setattr, config, attr, old)

    def make(self, folder: Path, name: str, age_days: float) -> Path:
        p = folder / name
        p.write_bytes(b"x" * 10)
        t = (NOW - timedelta(days=age_days)).timestamp()
        os.utime(p, (t, t))
        return p

    def make_video(self, num: str, age_days: float) -> None:
        for p in (f"本番_{num}_TikTok.mp4", f"本番_{num}_YouTube.mp4"):
            self.make(self.out, p, age_days)
        self.make(self.cache, f"本番_{num}_preview.mp4", age_days)
        self.make(self.cache, f"本番_{num}_title.jpg", age_days)
        for ext in ("txt", "timing.json", "wav", "mp3"):
            self.make(self.voice, f"本番_{num}.{ext}", age_days)

    # --- 何を消すか ---------------------------------------------------------
    def test_removes_only_heavy_files_older_than_30_days(self) -> None:
        self.make_video("040", 31)
        self.make_video("110", 1)

        gone = runner.purge_old(NOW)

        left = sorted(p.name for d in (self.out, self.cache, self.voice) for p in d.iterdir())
        self.assertEqual(sorted([
            "本番_040.txt", "本番_040.timing.json",          # 台本は残す（連番・作り直し用）
            "本番_110_TikTok.mp4", "本番_110_YouTube.mp4", "本番_110_preview.mp4",
            "本番_110_title.jpg", "本番_110.txt", "本番_110.timing.json",
            "本番_110.wav", "本番_110.mp3",
        ]), left)
        self.assertEqual({"本番_040"}, {name for name, _, _ in gone})
        self.assertEqual(6, len(gone))

    def test_unposted_videos_are_removed_too(self) -> None:
        """投稿したかどうかは見ない（2026-10-05 社長判断）。ログが無くても消える。"""
        self.make_video("075", 30.5)
        runner.purge_old(NOW)
        self.assertFalse((self.out / "本番_075_TikTok.mp4").exists())

    def test_files_just_under_30_days_are_kept(self) -> None:
        self.make_video("081", 29.9)
        self.assertEqual([], runner.purge_old(NOW))

    def test_zero_days_turns_it_off(self) -> None:
        self.make_video("040", 400)
        self.assertEqual([], runner.purge_old(NOW, days=0))
        self.assertTrue((self.out / "本番_040_TikTok.mp4").exists())

    def test_other_files_in_the_folders_are_never_touched(self) -> None:
        keep = [self.make(self.out, "サンプル.mp4", 400),
                self.make(self.voice, "narration_001.wav", 400),
                self.make(self.cache, "候補_20260804.json", 400)]
        runner.purge_old(NOW)
        self.assertTrue(all(p.exists() for p in keep))

    # --- 消した回を指定された時 ---------------------------------------------
    def test_purged_on_remembers_the_day(self) -> None:
        self.make_video("040", 31)
        runner.purge_old(NOW)
        self.assertEqual(datetime.now().strftime("%Y-%m-%d"), runner.purged_on("本番_040"))
        self.assertIsNone(runner.purged_on("本番_041"))

    def test_post_of_a_purged_video_explains_instead_of_posting_the_latest(self) -> None:
        self.make_video("040", 31)
        self.make_video("110", 1)
        runner.purge_old(NOW)
        ch = AsyncMock(spec=discord.TextChannel)

        self.assertTrue(asyncio.run(bot.refuse_purged(ch, "本番_040")))
        said = ch.send.call_args.args[0]
        self.assertIn("本番_040", said)
        self.assertIn("040 作って", said)

    def test_existing_or_unknown_videos_are_not_refused(self) -> None:
        """残っている回・掃除の記録が無い回（時刻の読み違い 18:30 → 030 等）は今までどおり。"""
        self.make_video("110", 1)
        ch = AsyncMock(spec=discord.TextChannel)
        self.assertFalse(asyncio.run(bot.refuse_purged(ch, "本番_110")))
        self.assertFalse(asyncio.run(bot.refuse_purged(ch, "本番_030")))
        self.assertFalse(asyncio.run(bot.refuse_purged(ch, None)))
        ch.send.assert_not_called()

    # --- 1日1回 --------------------------------------------------------------
    def test_purge_runs_once_a_day(self) -> None:
        with patch.object(runner, "purge_old", return_value=[]) as purge:
            asyncio.run(bot.purge_tick(NOW))
            asyncio.run(bot.purge_tick(NOW + timedelta(minutes=1)))
            self.assertEqual(1, purge.call_count)
            asyncio.run(bot.purge_tick(NOW + timedelta(days=1)))
            self.assertEqual(2, purge.call_count)

    def test_purge_waits_while_making_or_posting(self) -> None:
        async def busy_tick() -> None:
            async with bot._busy:
                await bot.purge_tick(NOW)

        with patch.object(runner, "purge_old", return_value=[]) as purge:
            asyncio.run(busy_tick())
            purge.assert_not_called()
            asyncio.run(bot.purge_tick(NOW))      # 空いたら同じ日のうちにやる
            purge.assert_called_once()

    def test_a_failed_purge_does_not_retry_all_day(self) -> None:
        with patch.object(runner, "purge_old", side_effect=OSError("boom")) as purge:
            asyncio.run(bot.purge_tick(NOW))
            asyncio.run(bot.purge_tick(NOW + timedelta(minutes=1)))
        self.assertEqual(1, purge.call_count)


class YoutubeAlreadyTest(TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        d = Path(self._tmp.name)
        for attr, value in (("CAPTION_DIR", d), ("SCRIPT_TXT_DIR", d),
                            ("STATE", d / "state.json")):
            old = getattr(config, attr)
            setattr(config, attr, value)
            self.addCleanup(setattr, config, attr, old)

    def test_reads_the_skip_line(self) -> None:
        log = "⏭ 既にアップロード済み: https://youtu.be/abc （2026-10-04T06:00:00）\n   二重に…"
        self.assertEqual("https://youtu.be/abc", runner.youtube_already(log))
        self.assertIsNone(runner.youtube_already("✅ アップロード完了: https://youtu.be/xyz"))

    def test_post_says_already_uploaded_and_does_not_call_codex(self) -> None:
        log = "⏭ 既にアップロード済み: https://youtu.be/abc （2026-10-04T06:00:00）"
        ch = AsyncMock(spec=discord.TextChannel)
        codex = AsyncMock()
        with patch.object(runner, "post_youtube", return_value=(True, log)), \
                patch.object(runner, "log_manual_tiktok"), \
                patch.object(bot, "thread_for", AsyncMock(return_value=None)), \
                patch.object(bot, "ask_codex", codex):
            asyncio.run(bot._do_post_inner(ch, "本番_104", "youtube", "am"))

        said = "\n".join(c.args[0] for c in ch.send.call_args_list)
        self.assertIn("上げ済み", said)
        self.assertIn("https://youtu.be/abc", said)
        self.assertNotIn("予約完了", said)
        codex.assert_not_called()


if __name__ == "__main__":
    main()
