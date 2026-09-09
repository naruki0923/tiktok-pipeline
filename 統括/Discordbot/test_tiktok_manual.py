"""TikTokを手動投稿にした分の受け渡し（2026-09-09）。

見るのは2つ。
  ・貼る投稿文をそのまま1行で取り出せること
  ・post_log.csv が途切れないこと（5_分析 がキャプションで回を突合するため）
"""
import csv
import tempfile
from pathlib import Path
from unittest import TestCase, main

import config
import runner

CAP = "【無料の個別相談はLINEから💬️】退職する月で変わる住民税 #退職 #住民税"


class TiktokCaptionTest(TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        for attr, value in (("CAPTION_DIR", self.dir),
                            ("POST_LOG", self.dir / "ログ" / "post_log.csv")):
            old = getattr(config, attr)
            setattr(config, attr, value)
            self.addCleanup(setattr, config, attr, old)

    def test_prefers_the_short_tiktok_file(self) -> None:
        (self.dir / "本番_072.txt").write_text("YouTube用の長い説明文\n" * 5, encoding="utf-8")
        (self.dir / "本番_072_TikTok.txt").write_text(CAP + "\n", encoding="utf-8")

        self.assertEqual(CAP, runner.tiktok_caption("本番_072"))

    def test_falls_back_to_the_shared_caption(self) -> None:
        (self.dir / "本番_072.txt").write_text(CAP + "\n", encoding="utf-8")

        self.assertEqual(CAP, runner.tiktok_caption("本番_072"))

    def test_collapses_newlines_into_one_line(self) -> None:
        """コードブロックに入れるので改行は潰す。まるごとコピーして貼れる形にする。"""
        (self.dir / "本番_072_TikTok.txt").write_text("タイトル\n#退職  #住民税\n",
                                                      encoding="utf-8")

        self.assertEqual("タイトル #退職 #住民税", runner.tiktok_caption("本番_072"))

    def test_empty_when_there_is_no_caption(self) -> None:
        self.assertEqual("", runner.tiktok_caption("本番_999"))


class ManualPostLogTest(TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.log = Path(self._tmp.name) / "ログ" / "post_log.csv"
        old = config.POST_LOG
        config.POST_LOG = self.log
        self.addCleanup(setattr, config, "POST_LOG", old)

    def rows(self) -> list[dict]:
        with self.log.open(encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def test_writes_a_row_the_analysis_side_can_match(self) -> None:
        """5_分析 は video 名とキャプションで回を突合する（update_results.caption_index）。"""
        self.assertTrue(runner.log_manual_tiktok("本番_072", CAP))

        row = self.rows()[0]
        self.assertEqual("本番_072_TikTok.mp4", row["video"])
        self.assertEqual(CAP, row["caption"])
        self.assertEqual(runner.MANUAL_STATUS, row["status"])

    def test_creates_the_header_on_a_fresh_file(self) -> None:
        runner.log_manual_tiktok("本番_072", CAP)

        first = self.log.read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual("datetime,video,status,url,caption", first)

    def test_appends_without_repeating_the_header(self) -> None:
        runner.log_manual_tiktok("本番_072", CAP)
        runner.log_manual_tiktok("本番_073", CAP)

        self.assertEqual(2, len(self.rows()))

    def test_status_never_blocks_a_later_automated_post(self) -> None:
        """post_tiktok.py は CONFIRMED_STATUSES を見て重複投稿を弾く。

        手動で渡しただけの行がそこに混ざると、あとで `おまかせ投稿` に
        切り替えた時に「もう投稿済み」と誤判定されて出せなくなる。
        """
        confirmed = {"予約_auto", "投稿_auto", "予約_semi_auto", "投稿_semi_auto"}

        self.assertNotIn(runner.MANUAL_STATUS, confirmed)

    def test_survives_an_unwritable_log(self) -> None:
        """ログが書けないだけで投稿文の受け渡しまで止めない。"""
        config.POST_LOG = Path(self._tmp.name) / "ファイル.txt" / "post_log.csv"
        Path(self._tmp.name, "ファイル.txt").write_text("じゃまもの", encoding="utf-8")

        self.assertFalse(runner.log_manual_tiktok("本番_072", CAP))


if __name__ == "__main__":
    main()
