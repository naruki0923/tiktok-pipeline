"""課金APIを使わず、台本のローカル正規化だけを確認する。"""
from unittest import TestCase, main

from write_script import MAX_TITLE_LINES, normalize_raw


class NormalizeRawTest(TestCase):
    def test_splits_inline_hook_tease_into_second_sentence(self) -> None:
        raw = "退職後に知っておきたい注意点3つ、特に最後の3つ目は、知っておかないと大損する内容です。"

        normalized = normalize_raw(raw)

        self.assertEqual(
            "退職後に知っておきたい注意点3つ。\n"
            "特に最後の3つ目は、知っておかないと大損する内容です。\n",
            normalized,
        )

    def test_keeps_existing_sentence_boundary(self) -> None:
        raw = "退職後に知っておきたい注意点3つ。特に最後の3つ目は、知っておかないと大損する内容です。"

        self.assertEqual(raw + "\n", normalize_raw(raw))

    def test_caps_four_short_title_phrases_at_three_without_dropping_words(self) -> None:
        raw = "毎日怒鳴られる、無視される、仕事を干される、それ全部パワハラの可能性があります。"

        normalized = normalize_raw(raw)
        first_sentence = normalized.split("。", 1)[0]

        self.assertEqual(MAX_TITLE_LINES, len(first_sentence.split("、")))
        self.assertEqual(
            "毎日怒鳴られる・無視される、仕事を干される、"
            "それ全部パワハラの可能性があります。\n",
            normalized,
        )

    def test_does_not_create_an_overlong_title_phrase(self) -> None:
        phrase = "1234567890123456"
        raw = "、".join([phrase] * 4) + "。"

        normalized = normalize_raw(raw)

        self.assertEqual(raw + "\n", normalized)


if __name__ == "__main__":
    main()
