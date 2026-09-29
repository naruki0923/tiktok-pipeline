"""65歳系（商材の対象外）の判定。止めすぎると対象年齢のネタまで落ちるので両側を見る。"""
from unittest import TestCase, main

from target_age import off_target


class OffTargetTest(TestCase):
    def test_catches_65_plus_topics(self) -> None:
        for text in ("65歳の誕生日の2日前までに退職しないと",
                     "６５歳を過ぎると一時金だけ",           # 全角数字
                     "64歳 65歳 退職 タイミング",
                     "70才からの働き方",
                     "65歳までに辞めないと損します",
                     "六十五歳",
                     "高年齢求職者給付金は何度でも",
                     "失業手当と年金を同時に受け取る",
                     "老齢厚生年金の繰上げ"):
            self.assertTrue(off_target(text), text)

    def test_leaves_target_age_topics_alone(self) -> None:
        for text in ("60歳到達時に申請すべき給付金",
                     "50代60代の再就職",
                     "退職後の国民年金は免除できます",
                     "住民税・国保・年金を下げる3つの申請",
                     "月収65万円でも",
                     "60歳から65歳まで支給される給付金",
                     "65歳未満なら対象です",
                     "65歳になるまで受け取れます",
                     "165歳",
                     "失業保険 知らないと損",
                     "",
                     None):
            self.assertEqual("", off_target(text), text)


if __name__ == "__main__":
    main()
