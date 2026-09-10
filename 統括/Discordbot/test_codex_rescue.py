"""救援が「動いたふり」をしないことを、Codexを起動せずに確かめる。"""
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch

import codex_rescue as cr

NEWER = ("The 'gpt-6-astra' model requires a newer version of Codex. "
         "Please upgrade to the latest app or CLI and try again.")
ERR_JSON = ('ERROR: {"type":"error","status":400,"error":'
            f'{{"type":"invalid_request_error","message":"{NEWER}"}}}}')
REPORT = "原因: VOICEVOXが起動していません\n変更内容: なし\n人間の操作が必要か: 必要"


class ChoosesTheNewerBinary(TestCase):
    """モデルを決めるのはChatGPT.appなので、CLIが古いと使えない。"""

    # 「ある」側は実在するパスで代用する（Pathのexistsは差し替えられない）
    HERE = Path(__file__).resolve()
    GONE = Path("/nonexistent/ChatGPT.app/codex")

    def _pick(self, path_ver, app_ver, app_exists=True):
        app = self.HERE if app_exists else self.GONE
        vers = {"/usr/bin/codex": path_ver, str(app): app_ver}
        with (patch.object(cr.shutil, "which", return_value="/usr/bin/codex"),
              patch.object(cr, "APP_CODEX", app),
              patch.object(cr, "_version", side_effect=lambda c: vers[c])):
            return cr.codex_bin()

    def test_prefers_the_app_bundle_when_it_is_newer(self):
        self.assertEqual(self._pick((0, 147, 0), (0, 153, 4)),
                         (str(self.HERE), (0, 153, 4)))

    def test_keeps_path_when_it_is_not_older(self):
        """同点なら人が入れた方を尊重する。"""
        self.assertEqual(self._pick((0, 153, 4), (0, 153, 4)),
                         ("/usr/bin/codex", (0, 153, 4)))
        self.assertEqual(self._pick((0, 160, 0), (0, 153, 4))[0], "/usr/bin/codex")

    def test_falls_back_to_path_without_the_app(self):
        self.assertEqual(self._pick((0, 147, 0), (), app_exists=False)[0],
                         "/usr/bin/codex")

    def test_reports_nothing_when_no_codex_at_all(self):
        with (patch.object(cr.shutil, "which", return_value=None),
              patch.object(cr, "APP_CODEX", self.GONE)):
            self.assertEqual(cr.codex_bin(), ("", ()))


class DetectsSilentFailure(TestCase):
    """codexは400でも終了コード0を返す。中身を見ないと失敗が分からない。"""

    def test_names_the_stale_cli_and_the_model(self):
        why = cr._why_failed(ERR_JSON, "/opt/homebrew/bin/codex", (0, 147, 0))
        self.assertIsNotNone(why)
        self.assertIn("gpt-6-astra", why)
        self.assertIn("0.147.0", why)
        self.assertIn("brew upgrade codex", why)

    def test_surfaces_other_api_errors_short(self):
        why = cr._why_failed('ERROR: {"type":"error","error":{"message":"rate limited"}}',
                             "/x/codex", (0, 153, 4))
        self.assertIn("rate limited", why)
        self.assertLess(len(why), 300)          # プロンプトのechoを貼り付けない

    def test_a_real_report_is_not_treated_as_failure(self):
        self.assertIsNone(cr._why_failed(REPORT, "/x/codex", (0, 153, 4)))

    def test_empty_output_is_not_treated_as_failure(self):
        self.assertIsNone(cr._why_failed("", "/x/codex", (0, 153, 4)))


if __name__ == "__main__":
    main()
