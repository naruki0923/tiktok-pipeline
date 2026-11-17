"""shots.build_spec（LLMの台本JSON → 3D spec）を外部通信なしで確かめる。

    cd 3_動画生成/3D && python3 -m unittest test_shots
"""
import copy
import json
import unittest
from pathlib import Path

import shots

EXAMPLE = json.loads((Path(__file__).parent / "examples" / "kokuho_src.json").read_text(encoding="utf-8"))


def src():
    return copy.deepcopy(EXAMPLE)


class BuildSpecTest(unittest.TestCase):
    def test_example_builds_with_rewind_and_cta(self):
        spec, errors, _ = shots.build_spec(src(), "本番_999")
        self.assertEqual(errors, [])
        parts = [s["part"] for s in spec["segments"]]
        self.assertEqual(parts[parts.index("rewind") - 1], "ideal")
        self.assertEqual(parts[parts.index("rewind") + 1], "real")
        self.assertEqual([s["say"][0] for s in spec["segments"][-3:]], shots.CTA_SAY)
        self.assertTrue(spec["segments"][-1].get("cta"))
        self.assertEqual(spec["note"], shots.NOTE)
        # もしもの世界は小字幕が固定・バッジ付き
        ideal = [s for s in spec["segments"] if s["part"] == "ideal"]
        self.assertTrue(all(s["sub"] == shots.IDEAL_SUB and s["badge"] == "もしもの世界" for s in ideal))

    def test_unknown_shot_is_error(self):
        s = src()
        s["segments"][1]["shot"] = "office_walk"      # もしもの世界で役所は使えない
        _, errors, _ = shots.build_spec(s, "x")
        self.assertTrue(any("office_walk" in e for e in errors))

    def test_money_must_not_increase(self):
        s = src()
        reals = [x for x in s["segments"] if x["part"] == "real"]
        reals[-1]["money"] = 119
        _, errors, _ = shots.build_spec(s, "x")
        self.assertTrue(any("増えています" in e for e in errors))

    def test_banned_like_and_follow_phrases(self):
        for line in ("いいねと保存してください", "退職のお金の話は毎朝この時間に"):
            s = src()
            s["segments"][2]["say"] = [line]
            _, errors, _ = shots.build_spec(s, "x")
            self.assertTrue(any("書かない言い回し" in e for e in errors), line)

    def test_punctuation_in_say_splits_lines(self):
        s = src()
        s["segments"][1]["say"] = ["退職して1日目、収入がゼロなので", "保険料もゼロ円です。"]
        spec, errors, _ = shots.build_spec(s, "x")
        self.assertEqual(errors, [])
        seg = [x for x in spec["segments"] if x["part"] == "ideal"][0]
        self.assertEqual(seg["say"], ["退職して1日目", "収入がゼロなので", "保険料もゼロ円です"])
        self.assertNotIn("。。", shots.script_text(spec))

    def test_decimal_is_not_split(self):
        s = src()
        s["segments"][1]["say"] = ["給付は最大1.5倍", "1,000人が使っています"]
        spec, errors, _ = shots.build_spec(s, "x")
        seg = [x for x in spec["segments"] if x["part"] == "ideal"][0]
        self.assertEqual(seg["say"], ["給付は最大1.5倍", "1,000人が使っています"])

    def test_fullwidth_percent_is_banned(self):
        s = src()
        s["segments"][2]["say"] = ["１００％受け取れます"]
        _, errors, _ = shots.build_spec(s, "x")
        self.assertTrue(any("断定" in e for e in errors))

    def test_hook_with_comma_becomes_too_many_lines(self):
        s = src()
        s["segments"][0]["say"] = ["もし退職後、国保が", "今の収入で、決まったら"]
        _, errors, _ = shots.build_spec(s, "x")
        self.assertTrue(any("フックは2〜3行" in e for e in errors))

    def test_morning_scene_is_not_banned(self):
        s = src()
        s["segments"][2]["say"] = ["毎朝ゆっくり起きられます"]
        _, errors, _ = shots.build_spec(s, "x")
        self.assertEqual(errors, [])

    def test_say_as_string_and_bad_segment(self):
        s = src()
        s["segments"][2]["say"] = "1か月後、届いた請求書はゼロ"
        spec, errors, _ = shots.build_spec(s, "x")
        self.assertEqual(errors, [])
        self.assertEqual([x for x in spec["segments"] if x["part"] == "ideal"][1]["say"],
                         ["1か月後", "届いた請求書はゼロ"])
        s["segments"][3] = "壊れた区間"
        _, errors, _ = shots.build_spec(s, "x")
        self.assertTrue(errors)

    def test_cta_written_by_llm_is_error(self):
        s = src()
        s["segments"][-1]["say"] = ["このアカウントをフォローして"]
        _, errors, _ = shots.build_spec(s, "x")
        self.assertTrue(any("CTA" in e for e in errors))

    def test_part_order(self):
        s = src()
        s["segments"] = [x for x in s["segments"] if x["part"] != "real"]
        _, errors, _ = shots.build_spec(s, "x")
        self.assertTrue(any("並び" in e for e in errors))

    def test_join_at_end_of_part_is_closed(self):
        s = src()
        last_ideal = max(i for i, x in enumerate(s["segments"]) if x["part"] == "ideal")
        s["segments"][last_ideal]["join"] = True
        spec, errors, _ = shots.build_spec(s, "x")
        self.assertEqual(errors, [])
        seg = [x for x in spec["segments"] if x["part"] == "ideal"][-1]
        self.assertNotIn("join", seg)

    def test_long_prop_is_error(self):
        s = src()
        s["props"]["office_sign"] = "とても長い看板の文字になってしまった"
        _, errors, _ = shots.build_spec(s, "x")
        self.assertTrue(any("props.office_sign" in e for e in errors))

    def test_script_text_sentences(self):
        spec, _, _ = shots.build_spec(src(), "x")
        text = shots.script_text(spec)
        self.assertTrue(text.startswith("もし退職後の国民健康保険料が、今の収入で決まったら。"))
        self.assertIn("でも現実は違います。", text)
        self.assertTrue(text.rstrip().endswith("無料の個別面談に参加してみてください。"))

    def test_narration_estimate_is_over_a_minute(self):
        spec, _, _ = shots.build_spec(src(), "x")
        self.assertGreater(shots.narration_seconds(spec), 60)


if __name__ == "__main__":
    unittest.main()
