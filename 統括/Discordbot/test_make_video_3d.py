"""3D（もしも型）の動画生成と、失敗した時の旧形式への切り替え。"""
import json
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch

import pipeline
import runner


class MakeVideo3DTest(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.p = patch.object(runner.config, "SCRIPT_TXT_DIR", self.dir)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def test_classic_when_no_spec(self):
        with (patch.object(runner, "make_video_classic", return_value=(True, "旧")) as c,
              patch.object(runner, "make_video_3d") as d):
            self.assertEqual(runner.make_video("本番_900"), (True, "旧"))
        c.assert_called_once()
        d.assert_not_called()

    def test_3d_success(self):
        (self.dir / "本番_900.3d.json").write_text("{}", encoding="utf-8")
        with (patch.object(runner, "make_video_3d", return_value=(True, "3D完了")),
              patch.object(runner, "make_video_classic") as c):
            self.assertEqual(runner.make_video("本番_900"), (True, "3D完了"))
        c.assert_not_called()

    def test_3d_failure_falls_back_to_classic(self):
        (self.dir / "本番_900.3d.json").write_text("{}", encoding="utf-8")
        with (patch.object(runner, "make_video_3d", return_value=(False, "…\nBlender の書き出しに失敗")),
              patch.object(runner, "make_video_classic", return_value=(True, "旧形式OK"))):
            ok, log = runner.make_video("本番_900")
        self.assertTrue(ok)
        self.assertTrue(log.startswith(runner.FALLBACK_MARK))
        self.assertIn("Blender の書き出しに失敗", log)

    def test_both_fail(self):
        (self.dir / "本番_900.3d.json").write_text("{}", encoding="utf-8")
        with (patch.object(runner, "make_video_3d", return_value=(False, "タイムアウト（7200秒）")),
              patch.object(runner, "make_video_classic", return_value=(False, "旧形式NG"))):
            ok, log = runner.make_video("本番_900")
        self.assertFalse(ok)
        self.assertIn("タイムアウト", log)

    def test_run_group_kills_process_group_on_timeout(self):
        proc = unittest_mock_proc()
        with (patch.object(runner.subprocess, "Popen", return_value=proc),
              patch.object(runner.os, "killpg") as killpg):
            ok, log = runner._run_group(["blender"], Path("."), 5)
        self.assertFalse(ok)
        self.assertTrue(log.startswith("タイムアウト"))
        killpg.assert_called_once()


def unittest_mock_proc():
    from unittest.mock import Mock
    proc = Mock(pid=999)
    proc.communicate.side_effect = [subprocess.TimeoutExpired(["blender"], 5), ("途中", None)]
    return proc


class PipelineStyleTest(TestCase):
    def test_write_script_uses_moshimo_in_3d_style(self):
        out = json.dumps({"ok": True, "name": "本番_900"})
        with (patch.object(pipeline.config, "VIDEO_STYLE", "3d"),
              patch.object(pipeline, "_run", return_value=subprocess.CompletedProcess([], 0, out, "")) as r):
            pipeline.write_script(Path("ref.txt"), {"url": "u"}, lambda _m: None)
        self.assertEqual(r.call_args.args[0][1], pipeline.config.WRITE_MOSHIMO)

    def test_write_script_classic(self):
        out = json.dumps({"ok": True, "name": "本番_900"})
        with (patch.object(pipeline.config, "VIDEO_STYLE", "classic"),
              patch.object(pipeline, "_run", return_value=subprocess.CompletedProcess([], 0, out, "")) as r):
            pipeline.write_script(Path("ref.txt"), {"url": "u"}, lambda _m: None)
        self.assertEqual(r.call_args.args[0][1], pipeline.config.WRITE_SCRIPT)

    def test_revise_3d_goes_through_moshimo(self):
        out = json.dumps({"ok": True, "title_lines": 2, "title": "t", "note": "n"})
        logs = []
        with (patch.object(runner, "script_txt", return_value=Path(__file__)),
              patch.object(runner, "is_3d", return_value=True),
              patch.object(runner, "remember"),
              patch.object(runner, "make_video", return_value=(True, "ok")),
              patch.object(pipeline, "_run", return_value=subprocess.CompletedProcess([], 0, out, "")) as r):
            pipeline.run_revise("本番_900", "短く", logs.append)
        cmd = r.call_args.args[0]
        self.assertEqual(cmd[1], pipeline.config.WRITE_MOSHIMO)
        self.assertIn("--revise", cmd)
        self.assertTrue(any("3D" in m for m in logs))

    def test_fallback_is_reported(self):
        logs = []
        with (patch.object(runner, "is_3d", return_value=True),
              patch.object(runner, "make_video",
                           return_value=(True, runner.FALLBACK_MARK + "\n3Dの失敗理由: x\n\n旧ログ"))):
            ok, _ = pipeline.make("本番_900", logs.append)
        self.assertTrue(ok)
        self.assertTrue(any(runner.FALLBACK_MARK in m for m in logs))


if __name__ == "__main__":
    main()
