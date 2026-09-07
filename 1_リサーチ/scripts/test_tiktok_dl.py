#!/usr/bin/env python3
"""tiktok_dl の再生URL抽出を外部アクセスなしで確認する。"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tiktok_dl import _SRC_JS, _has_audio_stream


class SourceExtractionTest(unittest.TestCase):
    def evaluate(self, scripts=(), videos=(), sigi_state=None):
        fixture = json.dumps({
            "scripts": list(scripts), "videos": list(videos),
            "sigiState": sigi_state,
        })
        harness = f'''const fixture = {fixture};
const location = {{pathname: '/@test/video/123'}};
const window = {{SIGI_STATE: fixture.sigiState}};
const document = {{querySelectorAll(selector) {{
  if (selector.startsWith('script')) return fixture.scripts.map(textContent => ({{textContent}}));
  return fixture.videos.map(src => ({{src, currentSrc: '', getAttribute: () => src}}));
}}}};
const performance = {{getEntriesByType: () => []}};
const result = {_SRC_JS};
console.log(JSON.stringify(result));'''
        proc = subprocess.run(
            ["node", "-e", harness], check=True, capture_output=True, text=True)
        return json.loads(proc.stdout)

    def test_embedded_json_keeps_fallbacks_and_prefers_h264(self):
        embedded = '''{"__DEFAULT_SCOPE__":{"webapp.video-detail":{"itemInfo":{"itemStruct":{
          "id":"123","video":{"bitrateInfo":[
            {"CodecType":"bytevc1","PlayAddr":{"UrlList":["https://cdn/h265.mp4"]}},
            {"CodecType":"h264","PlayAddr":{"UrlList":["https://cdn/h264.mp4"]}}
          ]}}
        }}}}'''
        result = self.evaluate(scripts=[embedded])
        self.assertEqual(result["urls"], ["https://cdn/h264.mp4", "https://cdn/h265.mp4"])

    def test_video_element_is_used_when_embedded_json_is_absent(self):
        result = self.evaluate(videos=["https://cdn/dom-video.mp4"])
        self.assertEqual(result["urls"], ["https://cdn/dom-video.mp4"])

    def test_alternate_json_shape_is_searched(self):
        alternate = '''{"ItemModule":{"123":{"id":"123","video":{
          "playAddr":"https://cdn/alt.mp4"}}}}'''
        result = self.evaluate(scripts=[alternate])
        self.assertEqual(result["urls"], ["https://cdn/alt.mp4"])

    def test_media_validation_requires_audio_stream(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "candidate.mp4"
            media.write_bytes(b"not real media")
            with patch("tiktok_dl.subprocess.run") as run:
                run.return_value.returncode = 0
                run.return_value.stdout = ""
                self.assertFalse(_has_audio_stream(media))

                run.return_value.stdout = "1\n"
                self.assertTrue(_has_audio_stream(media))


if __name__ == "__main__":
    unittest.main()
