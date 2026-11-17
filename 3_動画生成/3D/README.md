# 3D もしも型動画（毎朝の自動生成の本線）

2026-11-17 から、毎朝06:00の自動生成はこの3D形式で作る（社長判断・issue #28）。
参考は YouTube @Moshimo_Saigen の「もし◯◯したら？」の型。見た目と言い回しは丸写ししない。

```
もしもの世界（1日目→1か月→3か月→半年→1年）→ でも現実は違います（巻き戻し）
→ 現実（お金が減っていく）→ 今の制度で取り戻す（役所の窓口）→ 固定CTA
```

## 流れ

| 工程 | ファイル | 中身 |
|---|---|---|
| 台本＋場面 | `2_台本生成/scripts/write_moshimo.py` | 文字起こし＋`もしも型ルール.md`＋ショットの型 → LLM 1回（直しが要る時だけもう1回）→ `音声/本番_NNN.3d.json` と `本番_NNN.txt` |
| 型と検査 | `shots.py` | ショットの型（座標は固定）・LLMのJSON → spec・巻き戻しとCTAの自動挿入・尺の見積もり |
| 書き出し | `make_3d.py` | ナレーション（VOICEVOX）→ Blender（`world.py`）→ 字幕・カウンター・色味・巻き戻し → LINE誘導 → TikTok/YouTube 2本 |
| 3D空間 | `world.py` ＋ `scene_madoguchi.py` | 自宅・公園・役所を1つの空間に並べる。看板や書類の文字は spec の `props` で差し替え |

```bash
cd 3_動画生成/3D
../scripts/.venv/bin/python make_3d.py --check                              # 揃っているか
../scripts/.venv/bin/python make_3d.py ../音声/本番_150.3d.json --preview    # 静止画だけ（2分）
../scripts/.venv/bin/python make_3d.py ../音声/本番_150.3d.json              # 本番（1時間前後）
```

出力は旧形式と同じ `3_動画生成/output/本番_NNN_{TikTok,YouTube}.mp4`。投稿・30日削除・二重投稿防止はそのまま効く。
YouTube版は同じ動画に BGM（`assets/BGM`）を足しただけ。**SEは入れない**。

## 決めてあること

- **LLMに座標を書かせない**。カメラ・人物の置き場所は `shots.py` の型（1本目で見た目を確かめた値）から選ばせる。
  場面は自宅・公園・役所（看板は「ハローワーク」等に差し替え可）の3つだけ。描けない話は不採用。
- **事実**: もしもの世界は自由。現実・今の制度は参考動画の文字起こしにある事実だけ。使った事実は
  spec の `facts` に残り、Discordに「📌 使った制度の事実」として出る。文字起こしに無い数字は⚠️。
- **カウンター**: 貯金120万円・前年の年収400万円の40代、で固定。お金は減るだけ。「※数字はイメージです」を常に表示。
- **尺**: 必ず1分超え（ナレーション61〜75秒で検査。動画はこれ＋2秒前後）。60.5秒未満なら書き出さずに止める。
- 書かない: いいね保存の誘導・中盤フォロー・SE・65歳系（`target_age.py`）。
- 主人公は毎回同じ（Mixamo の Joe）。タイトル帯・色分け（もしも＝暖色／現実＝青く暗く）・バッジは固定。

## 失敗した日

Bot（`runner.make_video`）は3Dで作れなかったら **旧形式（散歩背景・`scripts/make_video.py`）で同じ台本から作り直す**。
Discordに「⚠️ 3Dで作れなかったので、今までの形式で作りました」と理由が出る。旧形式の仕組みは消さない。
3Dの制限時間は `RENDER_3D_TIMEOUT`（既定7200秒）。時間切れの時は Blender ごと止める。
`.env` に `VIDEO_STYLE=classic` と書けば、台本づくりから丸ごと旧形式（完コピ）に戻る。

## 前提

- `/Applications/Blender.app`（`brew install --cask blender`。5.3で確認）
- `3_動画生成/assets/3D/mixamo/` に hero/staff/customer と walk/idle/sad/head/talk/sit/look/think の .fbx
  （**Mixamo素材は再配布不可。リポジトリに入れない**。assets/ は gitignore 済み）
- VOICEVOX 起動中・`テンプレート/LINE誘導.mp4`・空き8GB以上
- 書き出しは M3 で1コマ2.3〜2.6秒（70秒の動画で1時間前後）。その間 Mac はほぼ全力で動く
- `work/` は作業フォルダ（gitignore）。完成したら連番は消す。2日より古い作業フォルダは次の書き出しで消える
