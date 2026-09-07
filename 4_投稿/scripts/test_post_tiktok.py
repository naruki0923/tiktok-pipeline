"""post_tiktok.py の外部送信を伴わない回帰テスト。"""
from __future__ import annotations

import ast
import csv
import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import post_tiktok


class SetCaptionTest(unittest.TestCase):
    def test_focuses_caption_without_pointer_click(self) -> None:
        """ポインター遮蔽中でもキャプション欄をクリックせずフォーカスする。"""
        page = Mock()
        element = Mock()

        with patch.object(post_tiktok, "_find_first", return_value=element):
            post_tiktok.set_caption(page, "本文 #退職")

        element.scroll_into_view_if_needed.assert_called_once_with()
        element.focus.assert_called_once_with()
        element.click.assert_not_called()
        page.keyboard.press.assert_any_call("Meta+A")
        page.keyboard.press.assert_any_call("Delete")
        page.keyboard.press.assert_any_call("Escape")


class SetFileViaCdpTest(unittest.TestCase):
    def test_finds_file_input_inside_shadow_dom(self) -> None:
        """TikTok StudioのShadow DOM内に移ったfile inputも設定できる。"""
        cdp = Mock()
        cdp.send.return_value = {
            "root": {
                "nodeName": "#document",
                "nodeId": 1,
                "shadowRoots": [{
                    "nodeName": "#document-fragment",
                    "nodeId": 2,
                    "children": [{
                        "nodeName": "INPUT",
                        "nodeId": 3,
                        "attributes": ["accept", "video/*", "type", "file"],
                    }],
                }],
            },
        }
        context = Mock()
        context.new_cdp_session.return_value = cdp

        result = post_tiktok.set_file_via_cdp(
            context, Mock(), "input[type='file']", "/tmp/video.mp4"
        )

        self.assertTrue(result)
        cdp.send.assert_any_call(
            "DOM.setFileInputFiles",
            {"files": ["/tmp/video.mp4"], "nodeId": 3},
        )
        cdp.detach.assert_called_once_with()


class CdpLifecycleTest(unittest.TestCase):
    def test_does_not_close_shared_cdp_browser(self) -> None:
        """CDP接続の終了時に共有Chromeへcloseを送らない。"""
        tree = ast.parse(inspect.getsource(post_tiktok.do_post))
        calls = [
            node for node in ast.walk(tree)
            if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "browser"
                and node.func.attr == "close")
        ]
        self.assertEqual([], calls)

    def test_recognizes_target_closed_error(self) -> None:
        page = Mock()
        page.is_closed.return_value = False
        error = post_tiktok.PWError(
            "Page.wait_for_selector: Target page, context or browser has been closed"
        )

        self.assertTrue(post_tiktok._target_was_closed(page, error))

    def test_posting_connection_uses_a_dedicated_new_page(self) -> None:
        p = Mock()
        browser = Mock()
        context = Mock()
        existing_page = Mock()
        posting_page = Mock()
        browser.contexts = [context]
        context.pages = [existing_page]
        context.new_page.return_value = posting_page
        p.chromium.connect_over_cdp.return_value = browser

        _, _, actual = post_tiktok.browser_ctx.connect(p, new_page=True)

        self.assertIs(posting_page, actual)
        context.new_page.assert_called_once_with()

    def test_metrics_fetch_does_not_close_shared_cdp_browser(self) -> None:
        source = (Path(__file__).parents[2] / "5_分析" / "scripts"
                  / "metrics_fetch.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        calls = [
            node for node in ast.walk(tree)
            if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "browser"
                and node.func.attr == "close")
        ]
        self.assertEqual([], calls)


class DuplicatePostGuardTest(unittest.TestCase):
    def test_finds_only_confirmed_entry_for_same_video(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "post_log.csv"
            with log.open("w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["datetime", "video", "status", "url", "caption"])
                writer.writerow(["2026-08-25T10:00:00", "本番_055_TikTok.mp4",
                                 "ready_semi_auto", "", "本文"])
                writer.writerow(["2026-08-25T11:00:00", "本番_055_TikTok.mp4",
                                 "予約_semi_auto", "", "本文"])
            with patch.object(post_tiktok, "LOG_CSV", log):
                actual = post_tiktok.confirmed_post_datetime(
                    Path("本番_055_TikTok.mp4")
                )
                other = post_tiktok.confirmed_post_datetime(
                    Path("本番_056_TikTok.mp4")
                )

        self.assertEqual("2026-08-25T11:00:00", actual)
        self.assertIsNone(other)


class ConfirmVerificationTest(unittest.TestCase):
    """確定の判定は「URLが変わった」ではなく「一覧に載った」で行う。"""

    CAPTION = ("【無料の個別相談はLINEから💬️】65歳を過ぎて退職しても何度でも"
               "受け取れる給付金 #退職 #失業保険 #給付金")

    def _page_showing(self, body: str):
        page = Mock()
        page.inner_text.return_value = body
        return page

    def test_caption_key_drops_shared_prefix_and_tags(self) -> None:
        """全動画で共通の【…】と#タグを外し、その動画固有の本文だけを鍵にする。"""
        self.assertEqual(
            "65歳を過ぎて退職しても何度でも受け取れる給付金",
            post_tiktok._caption_key(self.CAPTION),
        )

    def test_found_in_content_list(self) -> None:
        context = Mock()
        page = self._page_showing(
            "78件の投稿 【無料の個別相談はLINEから💬️】"
            "65歳を過ぎて退職しても何度でも受け取れる給付金 #退職"
        )
        context.new_page.return_value = page

        self.assertTrue(post_tiktok.post_exists_in_studio(context, self.CAPTION))
        page.close.assert_called_once_with()

    def test_absent_from_rendered_content_list(self) -> None:
        """一覧が描画されたのに載っていなければ未確定と判定する。"""
        context = Mock()
        page = self._page_showing("78件の投稿 別の動画のキャプション #退職")
        context.new_page.return_value = page

        self.assertFalse(post_tiktok.post_exists_in_studio(context, self.CAPTION))

    def test_unreachable_content_list_is_not_treated_as_confirmed(self) -> None:
        """一覧を確認できなかった時は None（＝確定扱いにしない）。"""
        context = Mock()
        page = Mock()
        page.goto.side_effect = post_tiktok.PWError("net::ERR_CONNECTION_RESET")
        context.new_page.return_value = page

        self.assertIsNone(post_tiktok.post_exists_in_studio(context, self.CAPTION))

    def test_semi_auto_confirmation_checks_the_content_list(self) -> None:
        """半自動の確定検知が post_exists_in_studio を通ること（URL判定だけにしない）。"""
        source = inspect.getsource(post_tiktok.do_post)
        tree = ast.parse(source)
        names = {
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertIn("post_exists_in_studio", names)
        self.assertIn("confirm_unverified", source)


class FileInputPickTest(unittest.TestCase):
    """mp4はフィードバック用のPDF欄ではなく動画欄に入れる。"""

    def test_prefers_the_video_accept_input(self) -> None:
        # 文書順ではPDF欄が先に来ても動画欄を選ぶ
        self.assertEqual(
            7, post_tiktok._pick_video_file_input([(3, "application/pdf"), (7, "video/*")])
        )

    def test_falls_back_to_input_without_accept(self) -> None:
        self.assertEqual(
            5, post_tiktok._pick_video_file_input([(3, "application/pdf"), (5, "")])
        )

    def test_returns_none_when_only_unrelated_inputs(self) -> None:
        self.assertIsNone(post_tiktok._pick_video_file_input([(3, "application/pdf")]))
        self.assertIsNone(post_tiktok._pick_video_file_input([]))


class ResumeEditModalTest(unittest.TestCase):
    """前回の未保存編集ダイアログが残っているとアップロードが始まらない。"""

    BODY = "編集していた動画は保存されませんでした。編集を続けますか？ 破棄する 続ける"

    def test_clicks_discard_when_modal_is_present(self) -> None:
        page = Mock()
        page.inner_text.return_value = self.BODY

        self.assertTrue(post_tiktok.dismiss_resume_edit_modal(page))
        page.locator.assert_called_with("button:has-text('破棄する')")

    def test_noop_when_modal_is_absent(self) -> None:
        page = Mock()
        page.inner_text.return_value = "アップロードする動画を選択"

        self.assertFalse(post_tiktok.dismiss_resume_edit_modal(page))
        page.locator.assert_not_called()

    def test_dismissed_before_waiting_for_the_file_input(self) -> None:
        source = inspect.getsource(post_tiktok.do_post)
        self.assertLess(
            source.index("dismiss_resume_edit_modal"),
            source.index("input[type='file']"),
        )


if __name__ == "__main__":
    unittest.main()
