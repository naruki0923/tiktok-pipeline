"""週次レビューが自動で書き換える2ファイルの検品。

検索語は**無人のリサーチが唯一入口にする値**なので、変な語を入れたら翌朝から
1本も作れなくなる。だから「怪しければ据え置く」を必ず守れているか見る。
"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main
from unittest.mock import patch

import weekly_review as wr

GOOD = ["退職給付金", "退職後 国民年金 免除", "60歳 給付金 申請", "失業保険 知らないと損"]


class CleanKeywordsTest(TestCase):
    def test_accepts_a_normal_list(self) -> None:
        self.assertEqual(GOOD, wr.clean_keywords(GOOD))

    def test_drops_duplicates(self) -> None:
        self.assertEqual(GOOD, wr.clean_keywords(GOOD + [GOOD[0]]))

    def test_rejects_too_few_or_too_many(self) -> None:
        self.assertEqual([], wr.clean_keywords(GOOD[:3]))
        self.assertEqual([], wr.clean_keywords([f"検索語{i}" for i in range(9)]))

    def test_rejects_a_junk_word_by_discarding_the_whole_list(self) -> None:
        """1語でも怪しければ全部捨てる（部分的に直すと気づかないまま効いてしまう）。"""
        for junk in ("https://example.com", "あ" * 21, "改行\n入り", ""):
            self.assertEqual([], wr.clean_keywords([*GOOD, junk]))

    def test_rejects_a_non_list(self) -> None:
        self.assertEqual([], wr.clean_keywords("退職給付金"))
        self.assertEqual([], wr.clean_keywords(None))


class OffTargetTest(TestCase):
    """65歳系は商材の対象外。週次が再生数で戻してきても反映させない。"""

    def test_drops_only_the_65_words(self) -> None:
        kws, dropped = wr.drop_off_target_keywords([*GOOD, "65歳 退職 失業保険"])

        self.assertEqual(GOOD, kws)
        self.assertEqual(["65歳 退職 失業保険"], dropped)

    def test_tops_up_from_current_keywords_when_too_few_remain(self) -> None:
        with patch.object(wr, "current_keywords",
                          return_value=["65歳 介護保険料 変わる", "退職 住民税 安くする"]):
            kws, _ = wr.drop_off_target_keywords(
                ["退職給付金", "65歳 退職 失業保険", "64歳 65歳 退職 タイミング",
                 "失業保険 知らないと損", "60歳 給付金 申請"])

        self.assertEqual(["退職給付金", "失業保険 知らないと損", "60歳 給付金 申請",
                          "退職 住民税 安くする"], kws)

    def test_focus_lines_about_65_are_removed(self) -> None:
        text = ("**優先する入口**\n"
                "1. 60歳到達時の給付金セット：432,412再生\n"
                "2. 65歳前後の申請タイミングずらし（失業手当×年金同時受給）：301,844再生\n"
                "3. 住民税・国保を下げる申請：257,123再生")

        kept, dropped = wr.drop_lines(text)

        self.assertNotIn("65歳", kept)
        self.assertIn("60歳到達時", kept)
        self.assertIn("住民税", kept)
        self.assertEqual(1, len(dropped))


class RunOffTargetTest(TestCase):
    """run() を通しで回し、65歳系が検索語.json・重点方針.md に書かれないこと。"""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.kw = Path(self.tmp.name) / "検索語.json"
        self.focus = Path(self.tmp.name) / "重点方針.md"
        for attr, value in (("KEYWORDS_JSON", self.kw), ("FOCUS_MD", self.focus)):
            original = getattr(wr, attr)
            setattr(wr, attr, value)
            self.addCleanup(setattr, wr, attr, original)
        for name in ("materials", "write_proposals", "write_report"):
            patcher = patch.object(wr, name, return_value=None if name != "materials" else "")
            patcher.start()
            self.addCleanup(patcher.stop)

    def _run(self, answer: dict) -> dict:
        with patch.object(wr.llm, "ask_json", return_value=answer):
            return wr.run(fetch=False)

    def test_writes_keywords_and_focus_without_65(self) -> None:
        res = self._run({"keywords": [*GOOD, "65歳 退職 失業保険"],
                         "focus": "1. 60歳到達時の給付金を優先する。手続き解説は書かない。\n"
                                  "2. 65歳前後の退職タイミングを今週も試す。\n"
                                  "3. 住民税・国保を下げる申請を続ける。"})

        saved = json.loads(self.kw.read_text(encoding="utf-8"))["keywords"]
        self.assertEqual(GOOD, saved)
        body = self.focus.read_text(encoding="utf-8")
        self.assertNotIn("65歳", body)
        self.assertIn("60歳到達時", body)
        self.assertTrue(any("65歳系の検索語は外しました" in w for w in res["warnings"]))
        self.assertTrue(any("重点方針から65歳系" in w for w in res["warnings"]))

    def test_bad_shape_still_removes_65_words_already_saved(self) -> None:
        self.kw.write_text(json.dumps({"keywords": [*GOOD, "65歳 介護保険料 変わる"]},
                                      ensure_ascii=False), encoding="utf-8")

        res = self._run({"keywords": ["https://example.com"]})

        self.assertEqual(GOOD, json.loads(self.kw.read_text(encoding="utf-8"))["keywords"])
        self.assertEqual(1, len(res["warnings"]))
        self.assertIn("据え置きました（65歳系の語だけは外しました）", res["warnings"][0])

    def test_keeps_keywords_when_too_few_would_remain(self) -> None:
        with patch.object(wr, "current_keywords", return_value=["65歳 退職 失業保険"]):
            res = self._run({"keywords": ["65歳 退職 失業保険", "64歳 65歳 退職 タイミング",
                                          "退職給付金", "失業保険 知らないと損"]})

        self.assertFalse(self.kw.exists())
        self.assertTrue(any("残りが足りない" in w for w in res["warnings"]))


class ApplyTest(TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.kw = Path(self.tmp.name) / "検索語.json"
        self.focus = Path(self.tmp.name) / "重点方針.md"
        for attr, value in (("KEYWORDS_JSON", self.kw), ("FOCUS_MD", self.focus)):
            original = getattr(wr, attr)
            setattr(wr, attr, value)
            self.addCleanup(setattr, wr, attr, original)

    def test_writes_keywords_and_keeps_the_old_ones(self) -> None:
        self.kw.write_text(json.dumps({"keywords": ["古い語1", "古い語2", "古い語3", "古い語4"]},
                                      ensure_ascii=False), encoding="utf-8")

        note = wr.apply_keywords(GOOD, dry=False)

        saved = json.loads(self.kw.read_text(encoding="utf-8"))
        self.assertEqual(GOOD, saved["keywords"])
        self.assertEqual(["古い語1", "古い語2", "古い語3", "古い語4"], saved["previous"])
        self.assertIn("検索語を更新", note)

    def test_dry_run_changes_nothing(self) -> None:
        wr.apply_keywords(GOOD, dry=True)

        self.assertFalse(self.kw.exists())

    def test_says_nothing_when_unchanged(self) -> None:
        self.kw.write_text(json.dumps({"keywords": GOOD}, ensure_ascii=False), encoding="utf-8")

        self.assertEqual("", wr.apply_keywords(GOOD, dry=False))

    def test_focus_is_written_with_a_header(self) -> None:
        note = wr.apply_focus("年齢トリガー（60歳）を今週も継続する。手続き解説は書かない。" * 2,
                              dry=False)

        body = self.focus.read_text(encoding="utf-8")
        self.assertIn("今週の重点方針", body)
        self.assertIn("60歳", body)
        self.assertIn("更新", note)

    def test_focus_ignores_an_empty_answer(self) -> None:
        self.assertEqual("", wr.apply_focus("", dry=False))
        self.assertEqual("", wr.apply_focus("短すぎ", dry=False))
        self.assertFalse(self.focus.exists())


if __name__ == "__main__":
    main()


class FocusShapeTest(TestCase):
    """重点方針は台本プロンプトへ丸ごと入る。見出しの二重・文の途中切れを防ぐ。"""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.focus = Path(self.tmp.name) / "重点方針.md"
        original = wr.FOCUS_MD
        wr.FOCUS_MD = self.focus
        self.addCleanup(setattr, wr, "FOCUS_MD", original)

    def test_drops_a_heading_the_llm_added_itself(self) -> None:
        wr.apply_focus("## 今週の重点方針（2026-09-07）\n\n"
                       + "年齢トリガー（60歳）を優先する。手続き解説は書かない。" * 2,
                       dry=False)

        body = self.focus.read_text(encoding="utf-8")
        self.assertEqual(1, body.count("今週の重点方針"))

    def test_cuts_at_a_line_break_not_mid_sentence(self) -> None:
        long_text = "\n".join(f"- {i}行目。年齢トリガーを優先する。" for i in range(200))

        wr.apply_focus(long_text, dry=False)

        body = self.focus.read_text(encoding="utf-8").strip()
        self.assertLess(len(body), wr.MAX_FOCUS_CHARS + 200)
        self.assertTrue(body.endswith("。"))
