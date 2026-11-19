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


class BrokenShapeTest(unittest.TestCase):
    def test_non_dict_segment_asks_fix_instead_of_crashing(self):
        bad = src()
        bad["segments"].append("壊れた区間")
        fixer = unittest.mock.Mock(return_value=src())
        with patch.object(wm, "measure", return_value=(66.0, "テスト")):
            wm.ask_until_ok(bad, "本番_999", fixer)
        fixer.assert_called_once()


class AsListTest(unittest.TestCase):
    def test_string_tags_are_split_not_per_char(self):
        self.assertEqual(wm._as_list("退職 失業保険、国保"), ["退職", "失業保険", "国保"])
        self.assertEqual(wm._as_list(["退職", ""]), ["退職"])


class FactWarningTest(unittest.TestCase):
    def test_number_missing_from_transcript_warns(self):
        s = src()
        s["segments"][-1]["say"] = ["受け取れるのは最長で1年6か月です", "まずは確かめてみてください"]
        spec, errors, _ = wm.shots.build_spec(s, "x", kind="bad")
        self.assertEqual(errors, [])
        self.assertEqual(wm.fact_warnings(spec, "通算して1年6か月まで受け取れます"), [])
        warns = wm.fact_warnings(spec, "受け取れる期間があります")
        self.assertTrue(any("1" in w for w in warns))

    def test_bad_future_numbers_are_not_facts(self):
        # 悪いもしも（real）は仮の話。貯金の額などは文字起こしに無くても警告しない
        spec, _, _ = wm.shots.build_spec(src(), "x", kind="bad")
        self.assertEqual(wm.fact_warnings(spec, "健康保険の制度です"), [])


class KindTest(unittest.TestCase):
    """新しく作る回は必ず悪いもしも。理想のもしも（147・148）は手直しの時だけ通す。"""

    LEGACY = json.loads((wm.THREE_D / "examples" / "kokuho_src.json").read_text(encoding="utf-8"))

    def test_new_script_in_ideal_shape_is_rejected(self):
        with patch.object(wm, "measure", return_value=(66.0, "テスト")):
            _, errors, _ = wm.check(copy.deepcopy(self.LEGACY), "本番_999")
        self.assertTrue(any("hook→real→system" in e for e in errors))

    def test_revise_keeps_ideal_shape(self):
        with patch.object(wm, "measure", return_value=(66.0, "テスト")):
            spec, errors, _ = wm.check(copy.deepcopy(self.LEGACY), "本番_999",
                                       kind=wm.shots.kind_of(self.LEGACY))
        self.assertEqual(errors, [])
        self.assertIn("rewind", [s["part"] for s in spec["segments"]])


class TitleTest(unittest.TestCase):
    def test_title_rules_are_the_conditions_only(self):
        text = wm.title_rules()
        self.assertTrue(text.startswith("## タイトルの条件"))
        self.assertIn("もし〇〇だったら？", text)

    def test_candidates_tolerate_broken_shapes(self):
        got = wm.candidates({"title_candidates": [{"title": "もし家賃が払えなくなったら？", "system": "住居確保給付金"},
                                                   "もし再就職先が見つからなかったら？", 3, {"title": ""}]})
        self.assertEqual([c["title"] for c in got], ["もし家賃が払えなくなったら？", "もし再就職先が見つからなかったら？"])
        self.assertEqual(got[0]["story"], "")
        self.assertEqual(wm.candidates({"title_candidates": "壊れた"}), [])

    def test_example_has_candidates(self):
        self.assertEqual(wm.candidates(src())[0]["title"], "もし病気で働けなくなったら？")


if __name__ == "__main__":
    unittest.main()
