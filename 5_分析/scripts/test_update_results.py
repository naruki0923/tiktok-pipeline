"""TikTok Studio の Content.csv → 実績（update_results.py）の読み取り。

週次レビューの実測は、社長が Studio から手で落とした CSV だけが入口（issue #14）。
列名は表示言語で変わり、日付には年が無いので、そこを間違えないことを見る。
"""
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main

import update_results as ur

# 動画ID 7681870521679564053 の上位32bit = 2026-09-05 11:20 JST（アップロード）。公開は翌朝
VID = "https://www.tiktok.com/@example/video/7681870521679564053"
CSV_EN = (
    '"Time","Video title","Video link","Post time","Total likes","Total comments","Total shares","Total views"\n'
    f'"9月13日","【無料の個別相談はLINEから💬️】退職後の住民税と国保を半額以下にする3つの申請 #退職","{VID}","9月6日","5,431","24","461","289568"\n'
)


def _write(dir_: str, name: str, text: str) -> Path:
    p = Path(dir_) / name
    p.write_text(text, encoding="utf-8")
    return p


class ParseContentCsvTest(TestCase):
    def test_reads_title_url_views_and_counts(self) -> None:
        with TemporaryDirectory() as d:
            rows = ur.parse_content_csv(_write(d, "Content.csv", CSV_EN))

        self.assertEqual(1, len(rows))
        r = rows[0]
        self.assertEqual(VID, r["url"])
        self.assertEqual(289568, r["views"])
        self.assertEqual(5431, r["likes"])
        self.assertTrue(r["title"].startswith("【無料の個別相談はLINEから"))

    def test_publish_date_comes_from_post_time_with_the_year_filled_in(self) -> None:
        """CSVの「9月6日」に年を補う。動画IDの日付（アップ日＝前日）ではない。"""
        with TemporaryDirectory() as d:
            rows = ur.parse_content_csv(_write(d, "Content.csv", CSV_EN))

        self.assertEqual("20260906", rows[0]["upload_date"])

    def test_year_rolls_over_when_posted_after_new_year(self) -> None:
        """12/31にアップして1/1に公開 → 翌年になる。"""
        self.assertEqual(datetime(2027, 1, 1),
                         ur._post_date("1月1日", datetime(2026, 12, 31, 23, 0)))
        self.assertEqual(datetime(2026, 9, 6),
                         ur._post_date("2026-09-06", datetime(2026, 9, 5)))

    def test_japanese_headers_are_found_by_keyword(self) -> None:
        csv_ja = ('"日付","動画タイトル","動画リンク","投稿日","いいね","コメント","シェア","再生数"\n'
                  f'"9月13日","タイトル","{VID}","9月6日","1","2","3","1.2K"\n')
        with TemporaryDirectory() as d:
            rows = ur.parse_content_csv(_write(d, "Content.csv", csv_ja))

        self.assertEqual(1200, rows[0]["views"])
        self.assertEqual("20260906", rows[0]["upload_date"])

    def test_missing_required_column_is_an_error_not_silence(self) -> None:
        with TemporaryDirectory() as d:
            p = _write(d, "Content.csv", '"Time","Total likes"\n"9月13日","1"\n')
            with self.assertRaises(ValueError):
                ur.parse_content_csv(p)

    def test_bom_and_rows_without_video_link_are_skipped(self) -> None:
        text = "﻿" + CSV_EN + '"9月13日","削除済み","","9月6日","0","0","0","0"\n'
        with TemporaryDirectory() as d:
            rows = ur.parse_content_csv(_write(d, "Content.csv", text))

        self.assertEqual(1, len(rows))


class LatestInboxTest(TestCase):
    def test_picks_the_newest_content_csv_and_ignores_overview(self) -> None:
        with TemporaryDirectory() as d:
            old = _write(d, "Content_20260906.csv", CSV_EN)
            new = _write(d, "Content_20260913.csv", CSV_EN)
            _write(d, "Overview_20260913.csv", "Date,Video Views\n")
            import os
            os.utime(old, (1, 1))
            orig, ur.INBOX = ur.INBOX, Path(d)
            try:
                self.assertEqual(new, ur.latest_inbox_csv())
            finally:
                ur.INBOX = orig

    def test_empty_inbox_is_none(self) -> None:
        with TemporaryDirectory() as d:
            orig, ur.INBOX = ur.INBOX, Path(d) / "nothing"
            try:
                self.assertIsNone(ur.latest_inbox_csv())
            finally:
                ur.INBOX = orig


if __name__ == "__main__":
    main()


class MergeRowsTest(TestCase):
    def test_same_video_keeps_the_larger_view_count(self) -> None:
        """7日版と60日版で同じ動画が出る。累計は増えるだけなので大きい方＝新しい方。"""
        a = [{"url": "u1", "views": 100}, {"url": "u2", "views": 5}]
        b = [{"url": "u1", "views": 120}, {"url": "u3", "views": 7}]

        got = {r["url"]: r["views"] for r in ur.merge_rows(a, b)}

        self.assertEqual({"u1": 120, "u2": 5, "u3": 7}, got)

    def test_rows_without_url_are_dropped(self) -> None:
        self.assertEqual([], ur.merge_rows([{"url": "", "views": 1}]))
