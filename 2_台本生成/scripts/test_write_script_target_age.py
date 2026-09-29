"""65歳系（商材の対象外）を write_script.py が台本プロンプトへ渡さない・書かせないこと。"""
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main
from unittest.mock import patch

import write_script as ws


class TargetAgeTest(TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_65_angles_go_to_off_target_not_winners(self) -> None:
        results = self.dir / "実績.tsv"
        results.write_text(
            "# 動画名\t公開日\t再生数\t角度\tタイトル\n"
            "本番_051\t2026-08-01\t301844\t65歳前後で失業手当と年金を同時受給\tx\n"
            "本番_050\t2026-08-01\t432412\t60歳到達時の給付金3つ\tx\n",
            encoding="utf-8")
        with patch.object(ws, "RESULTS", results):
            text = ws.covered_angles()

        winners, off = text.split("【対象外】")
        self.assertIn("60歳到達時", winners)
        self.assertNotIn("65歳", winners.split("【沈んだ角度】")[0])
        self.assertIn("65歳前後", off)

    def test_focus_note_drops_65_lines_left_in_the_file(self) -> None:
        focus = self.dir / "重点方針.md"
        focus.write_text("1. 60歳の給付金\n2. 65歳前後の申請タイミングずらし\n", encoding="utf-8")
        with patch.object(ws, "FOCUS", focus):
            note = ws.focus_note()

        self.assertIn("60歳", note)
        self.assertNotIn("65歳", note)

    def _run_main(self, res: dict) -> tuple[int, dict]:
        ref = self.dir / "ref_999.txt"
        ref.write_text("参考動画の文字起こし。" * 40, encoding="utf-8")
        out = io.StringIO()
        with patch.object(sys, "argv", ["write_script.py", "--ref", str(ref), "--name", "本番_999"]), \
                patch.object(ws.llm, "ask_json", return_value=res), \
                patch.object(ws, "run_format", side_effect=AssertionError("ここまで来てはいけない")), \
                redirect_stdout(out), self.assertRaises(SystemExit) as cm:
            ws.main()
        return cm.exception.code, json.loads(out.getvalue().strip().splitlines()[-1])

    def test_rejects_a_script_that_slipped_through_as_65(self) -> None:
        code, out = self._run_main({"ok": True, "script": "65歳の誕生日の前に退職すると。" * 20,
                                    "title": "退職のタイミング", "tags": ["退職"]})

        self.assertEqual(3, code)
        self.assertFalse(out["ok"])
        self.assertIn("65歳", out["reason"])

    def test_rejects_65_tags(self) -> None:
        code, out = self._run_main({"ok": True, "script": "退職後の国保は下げられます。" * 20,
                                    "title": "国保を下げる", "tags": ["退職", "65歳"]})

        self.assertEqual(3, code)
        self.assertFalse(out["ok"])


if __name__ == "__main__":
    main()
