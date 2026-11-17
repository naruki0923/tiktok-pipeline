"""make_3d.py のうち Blender を使わない所（歩く距離の調整）。

    cd 3_動画生成/3D && ../scripts/.venv/bin/python -m unittest test_make_3d
"""
import math
import unittest

import make_3d


def walk(dur):
    return {"start": 0.0, "end": dur, "cam": [1.4, -6.0, 1.7], "tgt": [0.0, -1.0, 1.1],
            "cam2": [1.4, -9.0, 1.7], "tgt2": [0.0, -4.5, 1.1],
            "hero": {"act": "walk", "at": [0.0, 1.0, 0], "to": [0.0, -5.2, 0]}}


class PaceWalksTest(unittest.TestCase):
    def test_long_segment_is_untouched(self):
        s = walk(6.0)
        make_3d.pace_walks([s])
        self.assertEqual(s["hero"]["at"], [0.0, 1.0, 0])
        self.assertEqual(s["cam"], [1.4, -6.0, 1.7])

    def test_short_segment_walks_shorter_and_keeps_the_end(self):
        s = walk(2.0)
        make_3d.pace_walks([s])
        h = s["hero"]
        self.assertEqual(h["to"], [0.0, -5.2, 0])
        self.assertAlmostEqual(math.dist(h["at"], h["to"]), make_3d.WALK_SPEED * 2.0, places=5)
        self.assertEqual(s["cam2"], [1.4, -9.0, 1.7])     # カメラの終点も同じ
        self.assertLess(abs(s["cam"][1] - s["cam2"][1]), 3.0)

    def test_non_walk_shot_is_ignored(self):
        s = {"start": 0, "end": 1, "hero": {"act": "idle", "at": [0, 0, 0]}}
        make_3d.pace_walks([s])
        self.assertEqual(s["hero"], {"act": "idle", "at": [0, 0, 0]})


if __name__ == "__main__":
    unittest.main()
