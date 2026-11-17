"""自宅・公園・役所の3か所を1つの3D空間に並べ、ショット表どおりにJPEG連番を書き出す。

  blender -b --factory-startup -P world.py -- <shots.json> <出力フォルダ> [preview]

shots.json: {"fps": 30, "total": 秒, "props": {...}, "segments": [ショット, ...]}
ショット: {"start","end","scene": "home"|"park"|"office","cam":[x,y,z],"tgt":[x,y,z],
          "cam2"?,"tgt2"?,"lens"?,"hero":{"act","at":[x,y,z],"rot":度,"to"?:[x,y,z]},
          "show"?:[小物グループ], "render": bool}
座標はその場所の原点からの相対。役所は scene_madoguchi.py の組み立てをそのまま使う。
props は場面の中の看板・書類の文字（shots.DEFAULT_PROPS と同じ形）。make_3d.py が渡す。
連番は JPEG（PNGだと70秒で6GB前後になり、Macの空きを圧迫するため）。
"""
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

HERE = Path(__file__).resolve().parent
_src = (HERE / "scene_madoguchi.py").read_text(encoding="utf-8")
_head = _src.split("# ---------------------------------------------------------------- カメラ")[0]
_g = {"__file__": str(HERE / "scene_madoguchi.py"), "__name__": "madoguchi"}
exec(compile(_head, "scene_madoguchi.py", "exec"), _g)
for _k, _v in _g.items():
    if not _k.startswith("__"):
        globals()[_k] = _v

ee = scene.eevee
if hasattr(ee, "taa_render_samples"):
    ee.taa_render_samples = 16
scene.render.image_settings.file_format = "JPEG"
scene.render.image_settings.quality = 95

PROPS = {
    "letter_ideal": ["保険料のお知らせ", "今月の請求　0"],
    "letter_real": ["納付通知書", "国民健康保険料"],
    "pc_ideal": ["求人　じっくり比較中", "やりたい仕事を探す"],
    "pc_real": ["急募！即日勤務OK", "未経験歓迎"],
    "office_sign": "市民窓口",
    "office_window": "国民健康保険",
    "office_notice": ["納付通知書", "国民健康保険料"],
    "office_doc": "受給資格者証",
}
PROPS.update(timing.get("props") or {})


def fitsize(body, size, width):
    """文字が板からはみ出さないように大きさを詰める（全角1字＝size 幅として見積もる）"""
    n = max(len(line) for line in str(body).split("\n")) or 1
    return min(size, width / n)


def retext(name, body, size, width):
    o = bpy.data.objects[name]
    o.data.body = body
    o.data.size = fitsize(body, size, width)

ORIGIN = {"office": Vector((0, 0, 0)), "home": Vector((40, 0, 0)), "park": Vector((-40, 0, 0))}
GROUPS = {}   # 小物グループ名 → [object]


def group(name, objs):
    GROUPS.setdefault(name, []).extend(objs)


def at(scene_name, xyz):
    return ORIGIN[scene_name] + Vector(xyz)


# ---------------------------------------------------------------- 役所をこの動画向けに並べ替え
# 主人公は正面の2番窓口へ（通路をまっすぐ歩ける）。2番の看板を話題の窓口に入れ替える。
_win = PROPS["office_window"]
_pool = [x for x in ("住民税", "国民健康保険", "年金", "雇用保険") if x != _win]
retext("T_住民税", _win, 0.32, 2.0)                   # 2番（正面）
retext("T_国民健康保険", _pool[0], 0.32, 2.0)          # 1番
retext("T_年金", _pool[1], 0.32, 2.0)                  # 3番
retext("T_市民窓口", PROPS["office_sign"], 0.62, 5.8)
retext("T_納付通知書", PROPS["office_notice"][0], 0.13, 0.72)   # カウンターの通知書
retext("T_住民税.001", PROPS["office_notice"][1], 0.09, 0.72)
box("shikakusho", (-0.6, 2.8, 1.055), (0.5, 0.36, 0.01), mat("doc_green", (0.80, 0.93, 0.82)))
text(PROPS["office_doc"], (-0.6, 2.82, 1.07), fitsize(PROPS["office_doc"], 0.075, 0.46), BLACK, rot=(0, 0, 0))
OFFICE_CAST = [o for o in bpy.data.objects
               if o.name.startswith(("staff", "cust", "wait")) or
               (o.parent and o.parent.name.startswith(("staff", "cust", "wait")))]


# ---------------------------------------------------------------- 自宅
def build_home():
    O = ORIGIN["home"]

    def B(name, loc, size, m):
        return box("home_" + name, O + Vector(loc), size, m)

    plank = [mat("plank1", (0.68, 0.50, 0.34), 0.7), mat("plank2", (0.62, 0.45, 0.30), 0.7)]
    for i in range(-8, 9):
        B(f"plank{i}", (i * 0.5, 0.5, -0.01), (0.49, 8.0, 0.02), plank[i % 2])
    wallc = mat("home_wall", (0.84, 0.78, 0.68))
    B("backwall", (0, 4.3, 2.0), (9, 0.2, 4.0), wallc)
    B("leftwall", (-4.2, 0.5, 2.0), (0.2, 8, 4.0), wallc)
    B("rightwall", (4.2, 0.5, 2.0), (0.2, 8, 4.0), wallc)
    B("skirting", (0, 4.18, 0.06), (8.4, 0.04, 0.12), WHITE)
    # 窓（空が見える）とカーテン
    B("window", (-1.3, 4.19, 1.75), (2.0, 0.04, 1.4), mat("sky", (0.62, 0.82, 0.98), emit=0.8))
    B("win_frame_t", (-1.3, 4.16, 2.47), (2.1, 0.06, 0.06), WHITE)
    B("win_frame_b", (-1.3, 4.16, 1.03), (2.1, 0.06, 0.06), WHITE)
    B("win_frame_m", (-1.3, 4.16, 1.75), (0.05, 0.06, 1.4), WHITE)
    cur = mat("curtain", (0.55, 0.70, 0.62))
    B("curtain_l", (-2.55, 4.12, 1.7), (0.45, 0.06, 1.7), cur)
    B("curtain_r", (-0.05, 4.12, 1.7), (0.45, 0.06, 1.7), cur)
    # ソファ（窓の前・こちら向き）
    sofa = mat("sofa", (0.30, 0.42, 0.62), 0.9)
    B("sofa_base", (-1.3, 3.35, 0.22), (2.4, 0.85, 0.44), sofa)
    B("sofa_back", (-1.3, 3.78, 0.65), (2.4, 0.22, 0.9), sofa)
    B("sofa_arm_l", (-2.55, 3.35, 0.5), (0.2, 0.85, 0.45), sofa)
    B("sofa_arm_r", (-0.05, 3.35, 0.5), (0.2, 0.85, 0.45), sofa)
    B("cushion", (-2.1, 3.55, 0.65), (0.45, 0.15, 0.4), mat("cushion", (0.95, 0.75, 0.35)))
    # ラグとローテーブル
    B("rug", (-1.3, 2.1, 0.005), (3.0, 2.0, 0.01), mat("rug", (0.85, 0.80, 0.70)))
    B("table", (-1.3, 2.15, 0.40), (1.5, 0.75, 0.06), WOOD)
    for dx in (-0.65, 0.65):
        for dy in (-0.3, 0.3):
            B(f"tleg{dx}{dy}", (-1.3 + dx, 2.15 + dy, 0.19), (0.05, 0.05, 0.38), WOOD)
    B("mug", (-1.8, 2.25, 0.48), (0.1, 0.1, 0.12), mat("mug", (0.95, 0.95, 0.95)))
    # テーブルの上の通知（もしも＝ゼロの請求書／現実＝納付通知書）
    ideal = [B("letter_ideal", (-1.15, 2.1, 0.435), (0.62, 0.44, 0.01), PAPER),
             B("letter_ideal_bar", (-1.15, 2.28, 0.442), (0.62, 0.07, 0.004), mat("blue_bar", (0.25, 0.55, 0.90)))]
    li, lr = PROPS["letter_ideal"], PROPS["letter_real"]
    ideal += [text(li[0], O + Vector((-1.15, 2.17, 0.45)), fitsize(li[0], 0.08, 0.58), BLACK, rot=(0, 0, 0)),
              text(li[1], O + Vector((-1.15, 2.01, 0.45)), fitsize(li[1], 0.11, 0.58),
                   mat("blue_deep", (0.04, 0.22, 0.62)), rot=(0, 0, 0))]
    real = [B("letter_real", (-1.15, 2.1, 0.435), (0.62, 0.44, 0.01), PAPER),
            B("letter_real_bar", (-1.15, 2.28, 0.442), (0.62, 0.07, 0.004), RED)]
    real += [text(lr[0], O + Vector((-1.15, 2.15, 0.45)), fitsize(lr[0], 0.09, 0.58), BLACK, rot=(0, 0, 0)),
             text(lr[1], O + Vector((-1.15, 2.0, 0.45)), fitsize(lr[1], 0.08, 0.58), RED, rot=(0, 0, 0))]
    group("letter_ideal", ideal)
    group("letter_real", real)
    # 机・椅子・ノートPC（右奥）
    B("desk", (2.5, 3.75, 0.74), (1.6, 0.7, 0.05), WOOD)
    for dx in (-0.75, 0.75):
        B(f"dleg{dx}", (2.5 + dx, 3.75, 0.36), (0.05, 0.6, 0.72), WOOD)
    B("chair_seat", (2.5, 3.05, 0.45), (0.5, 0.5, 0.06), mat("chair", (0.25, 0.25, 0.28)))
    B("chair_back", (2.5, 2.8, 0.75), (0.5, 0.05, 0.55), mat("chair", (0.25, 0.25, 0.28)))
    B("chair_leg", (2.5, 3.05, 0.22), (0.06, 0.06, 0.44), METAL)
    B("pc_base", (2.5, 3.7, 0.78), (0.55, 0.38, 0.02), METAL)
    scr = B("pc_screen", (2.5, 3.9, 1.0), (0.55, 0.02, 0.36), mat("pc_dark", (0.10, 0.10, 0.12)))
    scr.rotation_euler = (math.radians(-12), 0, 0)
    sc_ideal = [B("pc_glow_i", (2.5, 3.885, 1.0), (0.5, 0.005, 0.31), mat("pc_i", (0.85, 0.95, 1.0), emit=1.2))]
    pi, pr = PROPS["pc_ideal"], PROPS["pc_real"]
    sc_ideal += [text(pi[0], O + Vector((2.5, 3.875, 1.06)), fitsize(pi[0], 0.045, 0.46), mat("blue_t", (0.15, 0.40, 0.85))),
                 text(pi[1], O + Vector((2.5, 3.875, 0.96)), fitsize(pi[1], 0.04, 0.46), BLACK)]
    sc_real = [B("pc_glow_r", (2.5, 3.885, 1.0), (0.5, 0.005, 0.31), mat("pc_r", (1.0, 0.92, 0.85), emit=1.2))]
    sc_real += [text(pr[0], O + Vector((2.5, 3.875, 1.06)), fitsize(pr[0], 0.05, 0.46), RED),
                text(pr[1], O + Vector((2.5, 3.875, 0.96)), fitsize(pr[1], 0.04, 0.46), BLACK)]
    for o in sc_ideal + sc_real:   # 画面の傾き(-12度)に合わせる。文字は立てた向き(90度)から
        o.rotation_euler = (math.radians(78 if o.type == "FONT" else -12), 0, 0)
    group("pc_ideal", sc_ideal)
    group("pc_real", sc_real)
    # 本棚・植物・時計・カレンダー・テレビ
    B("shelf", (1.0, 4.05, 0.9), (1.0, 0.35, 1.8), mat("shelf", (0.85, 0.82, 0.76)))
    for k, c in enumerate([(0.8, 0.3, 0.3), (0.3, 0.5, 0.8), (0.9, 0.75, 0.3), (0.4, 0.7, 0.4)]):
        B(f"book{k}", (0.7 + k * 0.18, 3.9, 1.25), (0.14, 0.25, 0.32), mat(f"book{k}", c))
    cyl("home_pot", O + Vector((3.7, 2.0, 0.25)), 0.22, 0.5, POT)
    ico("home_leaf", O + Vector((3.7, 2.0, 0.85)), 0.42, PLANT)
    cyl("home_clock", O + Vector((2.5, 4.19, 2.5)), 0.25, 0.04, WHITE, verts=16).rotation_euler = (math.radians(90), 0, 0)
    B("calendar", (3.4, 4.19, 1.7), (0.5, 0.02, 0.65), WHITE)
    B("calendar_top", (3.4, 4.17, 1.93), (0.5, 0.02, 0.15), RED)
    B("tv", (-4.08, 1.5, 1.2), (0.06, 1.4, 0.8), BLACK)
    B("tvstand", (-3.9, 1.5, 0.25), (0.5, 1.6, 0.5), WOOD)
    bpy.ops.object.light_add(type="AREA", location=O + Vector((0, 1.5, 3.8)))
    lt = bpy.context.object
    lt.data.energy = 70
    lt.data.size = 6
    lt.data.color = (1.0, 0.95, 0.88)


# ---------------------------------------------------------------- 公園
def build_park():
    O = ORIGIN["park"]
    box("park_ground", O + Vector((0, 5, -0.05)), (60, 60, 0.1), mat("grass", (0.42, 0.66, 0.36), 0.9))
    box("park_path", O + Vector((0, 5, 0.005)), (1.8, 60, 0.02), mat("path", (0.86, 0.78, 0.62), 0.9))
    trunk = mat("trunk", (0.45, 0.32, 0.22))
    leafs = [mat("leaf_a", (0.30, 0.58, 0.32)), mat("leaf_b", (0.38, 0.66, 0.30)), mat("leaf_c", (0.25, 0.50, 0.30))]
    spots = [(-3.2, -6), (3.0, -4.5), (-3.6, -1), (3.4, 1.5), (-3.0, 3.5), (3.6, 6), (-3.4, 8), (3.0, 10.5),
             (-6.5, 2), (6.8, -1), (-7, 9), (7.5, 8), (-9, -4), (9, 3), (-5, 14), (5.5, 15)]
    for k, (x, y) in enumerate(spots):
        h = 1.4 + (k % 3) * 0.35
        cyl(f"trunk{k}", O + Vector((x, y, h / 2)), 0.14, h, trunk, verts=6)
        ico(f"canopy{k}", O + Vector((x, y, h + 0.7)), 1.0 + (k % 2) * 0.3, leafs[k % 3])
    bench = mat("pbench", (0.62, 0.42, 0.26))
    for k, y in enumerate((-2.5, 5.0)):
        box(f"pbench{k}", O + Vector((1.6, y, 0.45)), (0.5, 1.6, 0.07), bench)
        box(f"pbenchb{k}", O + Vector((1.85, y, 0.75)), (0.06, 1.6, 0.5), bench)
    pond = cyl("pond", O + Vector((6.5, 5.5, 0.01)), 3.0, 0.03, mat("pond", (0.35, 0.62, 0.85), 0.2), verts=12)
    pond.scale = (1.3, 1.0, 1.0)
    for k, y in enumerate((-3.5, 3.0, 9.5)):
        cyl(f"lamp{k}", O + Vector((-1.3, y, 1.4)), 0.05, 2.8, BLACK)
        ico(f"lamph{k}", O + Vector((-1.3, y, 2.85)), 0.16, mat("lampg", (1.0, 0.95, 0.8), emit=2.0))
    for k, x in enumerate(range(-24, 26, 6)):
        h = 6 + (k * 7) % 9
        box(f"bld{k}", O + Vector((x, 32, h / 2)), (5, 4, h), mat(f"bld{k % 3}", [(0.78, 0.80, 0.84), (0.70, 0.74, 0.80), (0.86, 0.84, 0.80)][k % 3]))


build_home()
build_park()

# ---------------------------------------------------------------- カメラとショット
bpy.ops.object.camera_add()
cam = bpy.context.object
scene.camera = cam
target = bpy.data.objects.new("target", None)
scene.collection.objects.link(target)
tc = cam.constraints.new("TRACK_TO")
tc.target = target
tc.track_axis = "TRACK_NEGATIVE_Z"
tc.up_axis = "UP_Y"

shots = timing["segments"]


def fr(sec):
    return max(1, min(FRAMES, int(round(sec * FPS)) + 1))


bounds = [fr(s["start"]) for s in shots] + [FRAMES + 1]


def key_const(obj, path, frame):
    obj.keyframe_insert(path, frame=frame)


for i, s in enumerate(shots):
    sc = s["scene"]
    f0, f1 = bounds[i], bounds[i + 1] - 1
    c0, t0 = at(sc, s["cam"]), at(sc, s["tgt"])
    c1 = at(sc, s["cam2"]) if "cam2" in s else c0.lerp(t0, 0.12)
    t1 = at(sc, s["tgt2"]) if "tgt2" in s else t0
    cam.location, target.location = c0, t0
    cam.keyframe_insert("location", frame=f0)
    target.keyframe_insert("location", frame=f0)
    cam.data.lens = s.get("lens", 26)
    cam.data.keyframe_insert("lens", frame=f0)
    cam.location, target.location = c1, t1
    cam.keyframe_insert("location", frame=max(f0, f1))
    target.keyframe_insert("location", frame=max(f0, f1))
    # 主人公の置き場所（歩くショットは to まで動かす）
    h = s["hero"]
    hero_root.location = at(sc, h["at"])
    hero_root.rotation_euler = (0, 0, math.radians(h.get("rot", 0)))
    hero_root.keyframe_insert("location", frame=f0)
    hero_root.keyframe_insert("rotation_euler", frame=f0)
    hero_root.location = at(sc, h.get("to", h["at"]))
    hero_root.keyframe_insert("location", frame=max(f0, f1))
    # 小物の出し分け（show に無いグループは隠す）
    for g, objs in GROUPS.items():
        hide = g not in s.get("show", [])
        for o in objs:
            o.hide_render = hide
            o.keyframe_insert("hide_render", frame=f0)
    # 役所の脇役は役所以外では隠す（書き出しを速くする）
    for o in OFFICE_CAST:
        o.hide_render = sc != "office"
        o.keyframe_insert("hide_render", frame=f0)


def all_curves(act):
    cs = list(getattr(act, "fcurves", []) or [])
    if not cs:
        cs = [c for ly in act.layers for st in ly.strips for bag in st.channelbags for c in bag.fcurves]
    return cs


# 区間の中はなめらか、区間の境目は切り替え（カメラのカット）
for o in [cam, target, hero_root, cam.data]:
    ad = o.animation_data
    if ad and ad.action:
        for c in all_curves(ad.action):
            for kp in c.keyframe_points:
                kp.interpolation = "BEZIER"
            pts = c.keyframe_points
            for k in range(len(pts) - 1):
                # 次のショットの頭と1フレームしか離れていない所は段差にする
                if pts[k + 1].co.x - pts[k].co.x <= 1.01:
                    pts[k].interpolation = "CONSTANT"
        if o in (hero_root,):
            for c in all_curves(ad.action):
                if c.data_path == "location":
                    for kp in c.keyframe_points:
                        if kp.interpolation == "BEZIER":
                            kp.interpolation = "LINEAR"   # 歩きは等速
                if c.data_path == "rotation_euler":
                    for kp in c.keyframe_points:
                        kp.interpolation = "CONSTANT"
for o in list(GROUPS_objs := [x for v in GROUPS.values() for x in v]) + OFFICE_CAST:
    ad = o.animation_data
    if ad and ad.action:
        for c in all_curves(ad.action):
            for kp in c.keyframe_points:
                kp.interpolation = "CONSTANT"


# ---------------------------------------------------------------- 動き
def play(arm, plan):
    ad = arm.animation_data_create()
    ad.action = None
    tr = ad.nla_tracks.new()
    for name, f0, f1, offset in plan:
        act = load_action(name)
        st = tr.strips.new(name, int(f0), act)
        if hasattr(st, "action_slot") and len(getattr(act, "slots", [])):
            st.action_slot = act.slots[0]
        a0, a1 = act.frame_range
        st.action_frame_start = a0 + offset * (a1 - a0)
        st.action_frame_end = a1
        span = st.action_frame_end - st.action_frame_start
        st.repeat = max(0.1, (f1 - f0) / max(1.0, span))
        st.extrapolation = "HOLD_FORWARD"


play(hero, [(s["hero"]["act"], bounds[i], bounds[i + 1], s["hero"].get("offset", 0.0))
            for i, s in enumerate(shots)])
play(staff1, [("idle", 1, FRAMES + 1, 0.0)])
play(staff2, [("talk", 1, FRAMES + 1, 0.0)])
play(cust1, [("talk", 1, FRAMES + 1, 0.3)])
play(wait1, [("sit", 1, FRAMES + 1, 0.0)])

# ---------------------------------------------------------------- 書き出し
scene.render.filepath = str(out_dir / "f_")
if preview:
    for i, s in enumerate(shots):
        if not s.get("render", True):
            continue
        scene.frame_set(int((bounds[i] + bounds[i + 1]) / 2))
        scene.render.filepath = str(out_dir / f"preview_{i:02d}.jpg")
        bpy.ops.render.render(write_still=True)
else:
    # 書き出しが要るショットだけ、連続する範囲ごとに書き出す（巻き戻しの区間は飛ばす）
    ranges = []
    for i, s in enumerate(shots):
        if not s.get("render", True):
            continue
        a, b = bounds[i], bounds[i + 1] - 1
        if ranges and ranges[-1][1] + 1 == a:
            ranges[-1][1] = b
        else:
            ranges.append([a, b])
    for a, b in ranges:
        scene.frame_start, scene.frame_end = a, b
        bpy.ops.render.render(animation=True)
