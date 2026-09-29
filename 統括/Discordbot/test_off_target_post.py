"""65歳系（商材の対象外）の動画は `投稿` でも出さない（2026-09-29 社長判断）。

作らなくする前に作った65歳系の動画が投稿待ちで残る。番号なしの `投稿` は
番号が一番大きい動画を選ぶので、投稿の入口で止まることを見る。
"""
import asyncio
import tempfile
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import AsyncMock, patch

import bot
import config
import runner


class OffTargetPostTest(TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        # 本物の台本・キャプション・state を読まないよう、全部一時フォルダへ向ける
        for attr, value in (("CAPTION_DIR", self.dir), ("SCRIPT_TXT_DIR", self.dir),
                            ("STATE", self.dir / "state.json")):
            old = getattr(config, attr)
            setattr(config, attr, value)
            self.addCleanup(setattr, config, attr, old)

    def test_reason_from_the_saved_title(self) -> None:
        runner.remember("本番_103", title="65歳前退職で失業手当と年金を同時に受け取る裏技")

        self.assertEqual("65歳", runner.off_target_reason("本番_103"))

    def test_reason_from_the_caption_when_state_has_nothing(self) -> None:
        (self.dir / "本番_100_TikTok.txt").write_text(
            "【無料の個別相談はLINEから💬️】64歳で年金を申請すると全額支給停止になる罠 #退職",
            encoding="utf-8")

        self.assertTrue(runner.off_target_reason("本番_100"))

    def test_reason_from_the_script(self) -> None:
        (self.dir / "本番_018.txt").write_text("6つ目\n年金\n65歳以上の人が対象の制度です\n",
                                               encoding="utf-8")

        self.assertEqual("65歳", runner.off_target_reason("本番_018"))

    def test_revised_video_is_judged_by_what_it_says_now(self) -> None:
        """`直して` で65歳の話を消した後は、作った時の角度が65歳系でも出せる。"""
        runner.remember("本番_103", title="退職日を1日ずらすだけで変わる社会保険料",
                        angle="65歳前退職→受給期間延長申請で失業手当と年金を同時受給")

        self.assertEqual("", runner.off_target_reason("本番_103"))

    def test_target_age_video_is_fine(self) -> None:
        runner.remember("本番_102", title="国民健康保険料を最大7割下げる減免制度",
                        angle="国保の法定軽減と自治体独自減免")

        self.assertEqual("", runner.off_target_reason("本番_102"))

    def test_post_is_refused_before_touching_youtube_or_tiktok(self) -> None:
        runner.remember("本番_103", title="65歳前退職で失業手当と年金を同時に受け取る裏技")
        ch = AsyncMock()
        with patch.object(runner, "post_youtube") as yt, \
                patch.object(runner, "post_tiktok") as tk, \
                patch.object(runner, "log_manual_tiktok") as log:
            asyncio.run(bot._do_post_inner(ch, "本番_103", "both", "am"))

        yt.assert_not_called()
        tk.assert_not_called()
        log.assert_not_called()
        self.assertIn("投稿しません", ch.send.call_args.args[0])

    def test_button_refuses_before_saying_it_will_post(self) -> None:
        runner.remember("本番_103", title="65歳前退職で失業手当と年金を同時に受け取る裏技")
        ch = AsyncMock()
        interaction = AsyncMock()

        async def press() -> None:
            button = bot.PostButton("本番_103", "both", "am", False)
            await button.callback(interaction)

        with patch.object(bot, "route", AsyncMock(return_value=ch)), \
                patch.object(bot, "do_post", AsyncMock()) as post:
            asyncio.run(press())

        post.assert_not_called()
        interaction.response.send_message.assert_not_called()
        self.assertIn("投稿しません", ch.send.call_args.args[0])
        self.assertIn("`103 直して", ch.send.call_args.args[0])


if __name__ == "__main__":
    main()
