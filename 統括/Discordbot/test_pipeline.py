"""autoパイプラインの候補補充ロジックを外部通信なしで確認する。"""
from pathlib import Path
import subprocess
import json
import tempfile
from unittest import TestCase, main
from unittest.mock import Mock, patch

import pipeline


def candidate(number: int) -> dict:
    return {
        "url": f"https://www.tiktok.com/@test/video/{number}",
        "title": f"候補{number}",
        "views": 100_000,
        "duration": 60,
    }


class RunAutoTest(TestCase):
    def test_research_requests_reserve_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)

            def run(cmd, cwd, timeout):
                out = Path(cmd[cmd.index("-o") + 1])
                out.write_text(json.dumps({"candidates": [candidate(1)]}),
                               encoding="utf-8")
                return subprocess.CompletedProcess(cmd, 0, "", "")

            with (patch.object(pipeline.config, "CACHE", cache),
                  patch.object(pipeline, "_run", side_effect=run) as mocked):
                actual = pipeline.research(lambda _message: None)

        self.assertEqual(len(actual), 1)
        cmd = mocked.call_args.args[0]
        self.assertEqual(cmd[cmd.index("--top") + 1],
                         str(pipeline.RESEARCH_CANDIDATE_LIMIT))

    def test_run_timeout_stops_process_group_and_becomes_pipeline_error(self):
        process = Mock(pid=4321)
        process.communicate.side_effect = [
            subprocess.TimeoutExpired(["use_ref.py"], 10),
            ("途中ログ", "停止工程"),
        ]
        with (patch.object(pipeline.subprocess, "Popen", return_value=process),
              patch.object(pipeline.os, "killpg") as killpg):
            with self.assertRaisesRegex(pipeline.PipelineError,
                                        "use_ref.py がタイムアウト"):
                pipeline._run(["python", "use_ref.py", "URL"], Path("."), 10)

        killpg.assert_called_once_with(4321, pipeline.signal.SIGKILL)
        self.assertEqual(process.communicate.call_count, 2)

    def test_audio_failures_are_replaced_by_later_candidates(self):
        candidates = [candidate(i) for i in range(1, 5)]
        adopted_urls = []

        def adopt(url, _log):
            adopted_urls.append(url)
            if url.endswith(("/2", "/3")):
                raise pipeline.PipelineError("取得失敗")
            return Path("ref_999.txt")

        results = [
            {"ok": False, "reason": "重複"},
            {"ok": True, "name": "本番_999", "title": "成功"},
        ]
        with (patch.object(pipeline, "research", return_value=candidates),
              patch.object(pipeline, "adopt", side_effect=adopt),
              patch.object(pipeline, "write_script", side_effect=results),
              patch.object(pipeline, "mark_rejected"),
              patch.object(pipeline.runner, "remember"),
              patch.object(pipeline.runner, "make_video", return_value=(True, ""))):
            actual = pipeline.run_auto(lambda _message: None)

        self.assertEqual(actual["name"], "本番_999")
        self.assertEqual(adopted_urls, [c["url"] for c in candidates])

    def test_reserve_candidate_can_complete_third_script_attempt(self):
        candidates = [candidate(i) for i in range(1, 8)]
        adopted_urls = []

        def adopt(url, _log):
            adopted_urls.append(url)
            if url.endswith(("/2", "/3", "/4", "/5")):
                raise pipeline.PipelineError("取得失敗")
            return Path("ref_999.txt")

        results = [
            {"ok": False, "reason": "重複1"},
            {"ok": False, "reason": "重複2"},
            {"ok": True, "name": "本番_999", "title": "成功"},
        ]
        with (patch.object(pipeline, "research", return_value=candidates),
              patch.object(pipeline, "adopt", side_effect=adopt),
              patch.object(pipeline, "write_script", side_effect=results),
              patch.object(pipeline, "mark_rejected"),
              patch.object(pipeline.runner, "remember"),
              patch.object(pipeline.runner, "make_video", return_value=(True, ""))):
            actual = pipeline.run_auto(lambda _message: None)

        self.assertEqual(actual["name"], "本番_999")
        self.assertEqual(adopted_urls, [c["url"] for c in candidates])

    def test_script_attempts_remain_capped_at_three(self):
        candidates = [candidate(i) for i in range(1, 5)]
        with (patch.object(pipeline, "research", return_value=candidates),
              patch.object(pipeline, "adopt", return_value=Path("ref_999.txt")) as adopt,
              patch.object(pipeline, "write_script",
                           return_value={"ok": False, "reason": "重複"}),
              patch.object(pipeline, "mark_rejected")):
            with self.assertRaises(pipeline.PipelineError):
                pipeline.run_auto(lambda _message: None)

        self.assertEqual(adopt.call_count, pipeline.MAX_SCRIPT_ATTEMPTS)


if __name__ == "__main__":
    main()
