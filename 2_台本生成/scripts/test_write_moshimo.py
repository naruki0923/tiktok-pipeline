"""write_moshimo.py の検査・直し（LLMは叩かない）。"""
import copy
import json
import unittest
from unittest.mock import patch

import write_moshimo as wm

EXAMPLE = json.loads(wm.EXAMPLE.read_text(encoding="utf-8"))


def src():
    return copy.deepcopy(EXAMPLE)


class AskUntilOkTest(unittest.TestCase):
    def test_ok_first_time_no_fix_call(self):
        fixer = unittest.mock.Mock()
        with patch.object(wm, "measure", return_value=(66.0, "テスト")):
            _, spec, _ = wm.ask_until_ok(src(), "本番_999", fixer)
        fixer.assert_not_called()
        self.assertEqual(spec["est_seconds"], 66.0)

    def test_broken_then_fixed_once(self):
        bad = src()
        bad["segments"][1]["shot"] = "office_talk"
        good = src()
        fixer = unittest.mock.Mock(return_value=good)
        with patch.object(wm, "measure", return_value=(66.0, "テスト")):
            _, spec, _ = wm.ask_until_ok(bad, "本番_999", fixer)
        fixer.assert_called_once()
        self.assertTrue(spec["segments"])

    def test_still_broken_raises(self):
        bad = src()
        bad["segments"][1]["shot"] = "office_talk"
        fixer = unittest.mock.Mock(return_value=bad)
        with patch.object(wm, "measure", return_value=(66.0, "テスト")):
            with self.assertRaises(ValueError):
                wm.ask_until_ok(bad, "本番_999", fixer)

    def test_too_short_asks_fix(self):
        fixer = unittest.mock.Mock(return_value=None)
        with patch.object(wm, "measure", return_value=(50.0, "テスト")):
            with self.assertRaisesRegex(ValueError, "短すぎ"):
                wm.ask_until_ok(src(), "本番_999", fixer)
        fixer.assert_called_once()

    def test_off_target_is_not_retried(self):
        s = src()
        s["segments"][0]["say"] = ["もし65歳の誕生日前に辞めたら", "どうなるのか"]
        fixer = unittest.mock.Mock()
        with patch.object(wm, "measure", return_value=(66.0, "テスト")):
            with self.assertRaisesRegex(ValueError, "65歳"):
                wm.ask_until_ok(s, "本番_999", fixer)
        fixer.assert_not_called()


class AsListTest(unittest.TestCase):
    def test_string_tags_are_split_not_per_char(self):
        self.assertEqual(wm._as_list("退職 失業保険、国保"), ["退職", "失業保険", "国保"])
        self.assertEqual(wm._as_list(["退職", ""]), ["退職"])


class FactWarningTest(unittest.TestCase):
    def test_number_missing_from_transcript_warns(self):
        spec, _, _ = wm.shots.build_spec(src(), "x")
        warns = wm.fact_warnings(spec, "前の年の給料を3割として計算します")
        self.assertEqual(warns, [])
        warns = wm.fact_warnings(spec, "軽減があります")
        self.assertTrue(any("3" in w for w in warns))


if __name__ == "__main__":
    unittest.main()
