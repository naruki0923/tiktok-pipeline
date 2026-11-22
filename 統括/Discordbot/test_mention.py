"""社長の操作待ちになったら名指しする（2026-11-22・issue #32）。

鳴らすのは「社長が何かしないと先へ進まない」時だけ。全部自動で済んだ時は鳴らさない。
"""
import asyncio
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import AsyncMock, MagicMock, patch

import bot
import config
import runner

OWNER = 1234567890
PING = f"<@{OWNER}>"


def _texts(ch: AsyncMock) -> list[str]:
    return [c.args[0] for c in ch.send.call_args_list if c.args]


class MentionTestBase(TestCase):
    def setUp(self) -> None:
        old = config.MENTION_USER_ID
        config.MENTION_USER_ID = OWNER
        self.addCleanup(setattr, config, "MENTION_USER_ID", old)
        self.ch = AsyncMock()
        for p in (patch.object(runner, "off_target_reason", return_value=""),
                  patch.object(runner, "tiktok_handed_on", return_value=""),
                  patch.object(runner, "tiktok_caption", return_value="キャプション #退職"),
                  patch.object(runner, "log_manual_tiktok"),
                  patch.object(runner, "schedule_close"),
                  patch.object(bot, "thread_for", AsyncMock(return_value=None)),
                  patch.object(bot, "ask_codex", AsyncMock(return_value=(False, [])))):
            p.start()
            self.addCleanup(p.stop)


class PostMentionTest(MentionTestBase):
    def post(self, where: str, auto: bool = False) -> list[str]:
        asyncio.run(bot._do_post_inner(self.ch, "本番_150", where, "am", auto))
        return _texts(self.ch)

    def test_manual_tiktok_pings(self) -> None:
        """TikTokは社長が貼って投稿する。YouTubeのアップロード待ちで離れていても気づけるように。"""
        with patch.object(runner, "post_youtube", return_value=(True, "ok")):
            texts = self.post("both")
        self.assertTrue(texts[0].startswith(PING))
        self.assertNotIn(PING, texts[1])          # コピーする投稿文には混ぜない

    def test_youtube_only_success_does_not_ping(self) -> None:
        with patch.object(runner, "post_youtube", return_value=(True, "ok")):
            texts = self.post("youtube")
        self.assertNotIn(PING, texts[0])

    def test_youtube_failure_pings(self) -> None:
        with patch.object(runner, "post_youtube", return_value=(False, "invalid_grant")):
            texts = self.post("youtube")
        self.assertTrue(texts[0].startswith(PING))

    def test_awaiting_the_schedule_button_pings(self) -> None:
        with patch.object(runner, "post_tiktok", return_value=("awaiting", "")), \
                patch.object(runner, "post_youtube", return_value=(True, "ok")):
            texts = self.post("both", auto=True)
        self.assertTrue(texts[0].startswith(PING))
        self.assertIn("【投稿予約する】", texts[0])

    def test_everything_confirmed_does_not_ping(self) -> None:
        with patch.object(runner, "post_tiktok", return_value=("confirmed", "")), \
                patch.object(runner, "post_youtube", return_value=(True, "ok")):
            texts = self.post("both", auto=True)
        self.assertNotIn(PING, texts[0])


class MakeMentionTest(MentionTestBase):
    def test_unreadable_background_pings(self) -> None:
        """macOSの【許可】を押す／素材を戻すのは社長。"""
        with patch.object(bot, "work_thread", AsyncMock(return_value=self.ch)), \
                patch.object(runner, "bg_dir_problem", return_value="⚠️ 背景素材が読めません"):
            asyncio.run(bot.do_make(self.ch, "auto"))
        self.assertTrue(_texts(self.ch)[-1].startswith(PING))

    def test_giving_up_on_retries_pings(self) -> None:
        old = config.AUTO_RETRY_MAX
        config.AUTO_RETRY_MAX = 0
        self.addCleanup(setattr, config, "AUTO_RETRY_MAX", old)
        with patch.object(bot, "work_thread", AsyncMock(return_value=self.ch)), \
                patch.object(runner, "bg_dir_problem", return_value=""), \
                patch.object(bot, "make_once",
                             AsyncMock(return_value=(None, "動画作成（auto）", "err"))):
            asyncio.run(bot.do_make(self.ch, "auto"))
        last = _texts(self.ch)[-1]
        self.assertTrue(last.startswith(PING))
        self.assertIn("止めます", last)


class WeeklyMentionTest(MentionTestBase):
    def run_weekly(self, res: dict) -> str:
        res = {"report": "/nowhere/週次_20261122.md", **res}   # 本物の weekly_review は必ず返す
        th = AsyncMock()
        with patch.object(bot, "open_thread", AsyncMock(return_value=th)), \
                patch.object(runner, "weekly_review", return_value=(True, res, "")), \
                patch.object(bot, "_mark_weekly_run"):
            asyncio.run(bot.do_weekly(self.ch, covers="2026-11-22"))
        return [t for t in _texts(th) if "週次レビュー完了" in t][0]

    def test_proposal_waiting_for_a_decision_pings(self) -> None:
        text = self.run_weekly({"summary": ["a"], "proposal": "/nowhere/ネタ提案_20261122.md"})
        self.assertTrue(text.startswith(PING))

    def test_nothing_to_decide_does_not_ping(self) -> None:
        text = self.run_weekly({"summary": ["a"]})
        self.assertNotIn(PING, text)

    def test_failure_pings(self) -> None:
        th = AsyncMock()
        with patch.object(bot, "open_thread", AsyncMock(return_value=th)), \
                patch.object(runner, "weekly_review", return_value=(False, {}, "boom")):
            asyncio.run(bot.do_weekly(self.ch, covers="2026-11-22"))
        self.assertTrue(_texts(th)[-1].startswith(PING))


class NoOwnerTest(TestCase):
    def test_no_owner_known_sends_plain_text(self) -> None:
        old = config.MENTION_USER_ID
        config.MENTION_USER_ID = 0
        self.addCleanup(setattr, config, "MENTION_USER_ID", old)
        with patch.object(runner, "load_state", return_value={}):
            self.assertEqual("本文", bot.needs_owner("本文"))


if __name__ == "__main__":
    main()
