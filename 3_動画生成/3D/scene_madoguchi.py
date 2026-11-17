"""役所の窓口の3D場面をBlenderで組んで、PNG連番に書き出す（試作）。

使い方（build.py から呼ばれる）:
  blender -b --factory-startup -P scene_madoguchi.py -- <timing.json> <出力フォルダ>

timing.json: {"fps": 30, "total": 秒, "segments": [{"start": 秒, "end": 秒, "slump": bool}, ...]}
素材のダウンロードはせず、箱・円柱・多面体だけでローポリの見た目を作る。
"""
import json
import math
import re
import sys
from pathlib import Path

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
timing = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
out_dir = Path(argv[1])
preview = len(argv) > 2 and argv[2] == "preview"
FPS = timing["fps"]
FRAMES = max(1, round(timing["total"] * FPS))
FONT = "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc"

# ---------------------------------------------------------------- 初期化
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.fps = FPS
scene.frame_start = 1
scene.frame_end = FRAMES
scene.render.resolution_x = 1080
scene.render.resolution_y = 1920
scene.render.resolution_percentage = 100
for eng in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
    try:
        scene.render.engine = eng
        break
    except TypeError:
        continue
ee = scene.eevee
for attr, val in (("taa_render_samples", 24), ("use_shadows", True),
                  ("use_raytracing", False), ("use_gtao", True)):
    if hasattr(ee, attr):
        setattr(ee, attr, val)
scene.view_settings.view_transform = "Standard"
for lk in ("Medium High Contrast", "None"):
    try:
        scene.view_settings.look = lk
        break
    except TypeError:
        continue
scene.render.image_settings.file_format = "PNG"
scene.render.filepath = str(out_dir / "f_")

world = bpy.data.worlds.new("W")
scene.world = world
world.use_nodes = True
bg = world.node_tree.nodes["Background"]
bg.inputs[0].default_value = (0.82, 0.86, 0.92, 1)
bg.inputs[1].default_value = 0.4

_mats = {}


def mat(name, rgb, rough=0.8, alpha=1.0, emit=0.0):
    key = (name, rgb, alpha, emit)
    if key in _mats:
        return _mats[key]
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    p = m.node_tree.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = (*rgb, 1)
    p.inputs["Roughness"].default_value = rough
    if alpha < 1.0:
        p.inputs["Alpha"].default_value = alpha
        if hasattr(m, "surface_render_method"):
            m.surface_render_method = "BLENDED"
        elif hasattr(m, "blend_method"):
            m.blend_method = "BLEND"
    if emit:
        p.inputs["Emission Color"].default_value = (*rgb, 1)
        p.inputs["Emission Strength"].default_value = emit
    _mats[key] = m
    return m


def flat(obj):
    for poly in obj.data.polygons:
        poly.use_smooth = False


def box(name, loc, size, m, parent=None):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc)
    o = bpy.context.object
    o.name = name
    o.scale = size
    o.data.materials.append(m)
    if parent:
        o.parent = parent
    return o


def cyl(name, loc, r, h, m, verts=8, parent=None):
    bpy.ops.mesh.primitive_cylinder_add(vertices=verts, radius=r, depth=h, location=loc)
    o = bpy.context.object
    o.name = name
    o.data.materials.append(m)
    flat(o)
    if parent:
        o.parent = parent
    return o


def ico(name, loc, r, m, parent=None):
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1, radius=r, location=loc)
    o = bpy.context.object
    o.name = name
    o.data.materials.append(m)
    flat(o)
    if parent:
        o.parent = parent
    return o


def text(body, loc, size, m, rot=(math.radians(90), 0, 0)):
    cu = bpy.data.curves.new("T", "FONT")
    cu.body = body
    cu.font = bpy.data.fonts.load(FONT, check_existing=True)
    cu.size = size
    cu.align_x = "CENTER"
    cu.align_y = "CENTER"
    cu.extrude = 0.004
    o = bpy.data.objects.new("T_" + body, cu)
    scene.collection.objects.link(o)
    o.location = loc
    o.rotation_euler = rot
    o.data.materials.append(m)
    return o


# ---------------------------------------------------------------- 色
FLOOR = mat("floor", (0.62, 0.62, 0.58), 0.6)
FLOOR2 = mat("floor2", (0.52, 0.53, 0.50), 0.6)
WALL = mat("wall", (0.80, 0.77, 0.70))
WOOD = mat("wood", (0.62, 0.45, 0.30))
TOP = mat("top", (0.96, 0.96, 0.94), 0.4)
ACRYL = mat("acryl", (0.75, 0.88, 0.95), 0.1, alpha=0.35)
WHITE = mat("white", (0.98, 0.98, 0.98))
BLACK = mat("black", (0.08, 0.08, 0.09))
SKIN = mat("skin", (0.95, 0.78, 0.64))
HAIR = mat("hair", (0.12, 0.10, 0.09))
GRAYHAIR = mat("grayhair", (0.55, 0.55, 0.55))
SHIRT = mat("shirt", (0.70, 0.78, 0.90))
VEST = mat("vest", (0.20, 0.30, 0.50))
NAVY = mat("navy", (0.14, 0.17, 0.26))
BEIGE = mat("beige", (0.85, 0.45, 0.20))
BENCH = mat("bench", (0.20, 0.42, 0.68))
METAL = mat("metal", (0.60, 0.62, 0.65), 0.3)
PLANT = mat("plant", (0.25, 0.55, 0.30))
POT = mat("pot", (0.85, 0.85, 0.82))
SIGN_COLORS = [(0.16, 0.42, 0.75), (0.15, 0.55, 0.38), (0.88, 0.50, 0.15)]
LED = mat("led", (1.0, 0.25, 0.1), emit=4.0)
PAPER = mat("paper", (1.0, 0.99, 0.95), 0.9)
RED = mat("red", (0.80, 0.12, 0.12))

# ---------------------------------------------------------------- 床・壁
for i in range(-6, 7):
    for j in range(-3, 9):
        box(f"tile{i}_{j}", (i * 1.0, j * 1.0, -0.01), (0.98, 0.98, 0.02),
            FLOOR if (i + j) % 2 == 0 else FLOOR2)
box("backwall", (0, 4.6, 4.0), (16, 0.2, 8.0), WALL)
box("baseboard", (0, 4.48, 0.08), (14, 0.05, 0.16), WOOD)
box("leftwall", (-6.5, 2.0, 4.0), (0.2, 13, 8.0), WALL)
box("ceil_beam", (0, 2.5, 3.6), (14, 0.3, 0.15), WHITE)

# ---------------------------------------------------------------- カウンター（窓口3つ）
COUNTER_Y = 3.0
box("counter", (0, COUNTER_Y, 0.5), (9.0, 0.7, 1.0), WOOD)
box("countertop", (0, COUNTER_Y - 0.05, 1.02), (9.2, 0.85, 0.05), TOP)
labels = ["国民健康保険", "住民税", "年金"]
for k, x in enumerate([-3.0, 0.0, 3.0]):
    # 仕切りのアクリル板
    for dx in (-1.5, 1.5):
        box(f"part{k}{dx}", (x + dx, COUNTER_Y - 0.1, 1.45), (0.03, 0.6, 0.8), ACRYL)
    # 吊り看板
    sc = mat(f"sign{k}", SIGN_COLORS[k], 0.6)
    box(f"sign{k}", (x, 4.0, 2.75), (2.2, 0.08, 0.55), sc)
    cyl(f"wire{k}a", (x - 0.9, 4.0, 3.2), 0.008, 0.4, BLACK)
    cyl(f"wire{k}b", (x + 0.9, 4.0, 3.2), 0.008, 0.4, BLACK)
    text(labels[k], (x, 3.95, 2.75), 0.32, WHITE)
    text(f"{k + 1}番", (x, COUNTER_Y - 0.43, 0.75), 0.22, WHITE)
    # 書類・トレイ
    box(f"tray{k}", (x + 0.7, COUNTER_Y - 0.1, 1.07), (0.4, 0.3, 0.05), METAL)
    box(f"papers{k}", (x - 0.6, COUNTER_Y + 0.05, 1.07), (0.35, 0.25, 0.04), PAPER)

# 壁の上の大きな表示（全景で上が空かないように）
box("bigsign", (0, 4.48, 4.05), (6.4, 0.06, 0.95), mat("navy_sign", (0.12, 0.20, 0.38), 0.6))
text("市民窓口", (0, 4.43, 4.1), 0.62, WHITE)
for k, x in enumerate((-5.0, 5.0)):
    box(f"poster{k}", (x, 4.48, 3.7), (1.1, 0.03, 1.5), mat(f"poster{k}", [(0.95, 0.85, 0.40), (0.55, 0.78, 0.92)][k]))
    text(["届出は\nお早めに", "マイナ\nカード"][k], (x, 4.45, 3.75), 0.26, BLACK)
# 受付番号の電光表示
box("numboard", (4.6, 4.45, 2.3), (1.2, 0.1, 0.7), BLACK)
text("受付番号", (4.6, 4.38, 2.52), 0.14, WHITE)
text("023", (4.6, 4.38, 2.22), 0.32, LED)
# 時計
cyl("clock", (-4.6, 4.45, 2.6), 0.3, 0.06, WHITE, verts=16).rotation_euler = (math.radians(90), 0, 0)
cyl("clockrim", (-4.6, 4.48, 2.6), 0.33, 0.04, BLACK, verts=16).rotation_euler = (math.radians(90), 0, 0)
# 観葉植物
for px in (-5.6, 5.6):
    cyl(f"pot{px}", (px, 3.9, 0.25), 0.25, 0.5, POT)
    ico(f"leaf{px}", (px, 3.9, 0.95), 0.5, PLANT)
    ico(f"leaf2{px}", (px + 0.15, 3.85, 1.35), 0.32, PLANT)

# ---------------------------------------------------------------- 人（Mixamo）
MIX = Path(__file__).resolve().parent.parent / "assets" / "3D" / "mixamo"
_actions = {}
PREFIX = re.compile(r"mixamorig\d*:")


def _import(path):
    before_o, before_a = set(bpy.data.objects), set(bpy.data.actions)
    bpy.ops.import_scene.fbx(filepath=str(path))
    return set(bpy.data.objects) - before_o, set(bpy.data.actions) - before_a


def load_action(name):
    """動きだけのFBXを読み込んで action を返す（骨組みは捨てる）"""
    if name not in _actions:
        objs, acts = _import(MIX / f"{name}.fbx")
        act = acts.pop()
        act.name = "A_" + name
        # 骨の名前の頭（mixamorig7: など）は人物ごとに違うので共通の mixamorig: にそろえる
        curves = list(getattr(act, "fcurves", []) or [])
        if not curves:
            curves = [c for ly in act.layers for st in ly.strips for bag in st.channelbags for c in bag.fcurves]
        for c in curves:
            c.data_path = PREFIX.sub("mixamorig:", c.data_path)
        act.use_fake_user = True
        for o in objs:
            bpy.data.objects.remove(o, do_unlink=True)
        _actions[name] = act
    return _actions[name]


def load_char(fbx, name, loc, rot_deg):
    """人物FBXを読み込み、空の親(root)の下に置く。向きと位置は root で決める。
    Mixamo の人物は -Y（カメラ側）を向いて読み込まれる。"""
    objs, acts = _import(MIX / fbx)
    for a in acts:  # 人物FBXに付いてくる T ポーズの action は使わない
        bpy.data.actions.remove(a)
    arm = next(o for o in objs if o.type == "ARMATURE")
    arm.name = name
    for b in arm.data.bones:   # 頂点グループも一緒に名前が変わる
        b.name = PREFIX.sub("mixamorig:", b.name)
    root = bpy.data.objects.new(name + "_root", None)
    scene.collection.objects.link(root)
    arm.parent = root
    root.location = loc
    root.rotation_euler = (0, 0, math.radians(rot_deg))
    for o in objs:
        if o.type == "MESH":
            for slot in o.material_slots:   # テカりを消してマットに
                m = slot.material
                if m and m.use_nodes:
                    for nd in m.node_tree.nodes:
                        if nd.type == "BSDF_PRINCIPLED":
                            for key, val in (("Roughness", 0.85), ("Specular IOR Level", 0.15),
                                             ("Metallic", 0.0), ("Coat Weight", 0.0)):
                                inp = nd.inputs.get(key)
                                if inp is not None:
                                    for ln in list(inp.links):
                                        m.node_tree.links.remove(ln)
                                    inp.default_value = val
    return root, arm


CY = COUNTER_Y
HERO_AT = (0.0, CY - 1.1, 0)
hero_root, hero = load_char("hero.fbx", "hero", HERO_AT, 180)
staff1 = load_char("staff.fbx", "staff1", (-3.0, CY + 0.7, 0), 0)[1]
staff2 = load_char("staff.fbx", "staff2", (0.0, CY + 0.7, 0), 0)[1]
cust1 = load_char("customer.fbx", "cust1", (-3.0, CY - 1.0, 0), 180)[1]
wait1 = load_char("customer.fbx", "wait1", (2.4, 0.05, 0.02), 180)[1]
# 3番窓口は職員なし
box("closed", (3.0, CY - 0.05, 1.25), (0.9, 0.04, 0.4), WHITE)
text("受付休止中", (3.0, CY - 0.08, 1.25), 0.12, RED)

# カウンターに置いた通知書（肩越しショットで見せる）
box("notice", (0.35, 2.75, 1.055), (0.8, 0.56, 0.01), PAPER)
box("notice_bar", (0.35, 2.96, 1.062), (0.8, 0.09, 0.004), RED)
text("納付通知書", (0.35, 2.76, 1.07), 0.13, BLACK, rot=(0, 0, 0))
text("住民税", (0.35, 2.6, 1.07), 0.09, RED, rot=(0, 0, 0))

# ---------------------------------------------------------------- 待合ベンチ
for row, y in enumerate([0.2, -1.4]):
    for bx in (-2.6, 2.6):
        box(f"bench{row}{bx}", (bx, y, 0.45), (3.4, 0.5, 0.08), BENCH)
        box(f"back{row}{bx}", (bx, y - 0.25, 0.75), (3.4, 0.06, 0.55), BENCH)
        for lx in (-1.4, 1.4):
            box(f"leg{row}{bx}{lx}", (bx + lx, y, 0.22), (0.06, 0.4, 0.44), METAL)
# ---------------------------------------------------------------- 光
bpy.ops.object.light_add(type="SUN", location=(4, -3, 8))
sun = bpy.context.object
sun.data.energy = 2.4
sun.rotation_euler = (math.radians(50), math.radians(10), math.radians(35))
if hasattr(sun.data, "angle"):
    sun.data.angle = math.radians(8)
bpy.ops.object.light_add(type="AREA", location=(0, 1.0, 3.4))
area = bpy.context.object
area.data.energy = 160
area.data.size = 8

# ---------------------------------------------------------------- カメラ（後ろの高い位置から主人公へゆっくり寄る）
bpy.ops.object.camera_add()
cam = bpy.context.object
scene.camera = cam
cam.data.lens = 26
target = bpy.data.objects.new("target", None)
scene.collection.objects.link(target)
tc = cam.constraints.new("TRACK_TO")
tc.target = target
tc.track_axis = "TRACK_NEGATIVE_Z"
tc.up_axis = "UP_Y"

segs = timing["segments"]


def fr(sec):
    return max(1, min(FRAMES, int(round(sec * FPS)) + 1))


# 区間ごとにカメラ位置を変えて、ずっとゆっくり動かし続ける
shots = [
    ((0.0, -6.5, 4.6), (0.0, 2.6, 1.3)),   # 待合の後ろから全景
    ((1.6, -3.6, 3.6), (0.0, 2.6, 1.0)),   # 窓口へ寄る
    ((1.6, 0.6, 2.6), (0.2, 2.7, 1.1)),  # 主人公の肩越し（通知書）
    ((-2.2, 0.2, 2.4), (0.0, 2.3, 1.3)),   # 斜め後ろ・うなだれる
]
for i, seg in enumerate(segs):
    (cp, tp) = shots[min(i, len(shots) - 1)]
    nxt = shots[min(i + 1, len(shots) - 1)]
    f0, f1 = fr(seg["start"]), fr(seg["end"])
    cam.location = cp
    cam.keyframe_insert("location", frame=f0)
    target.location = tp
    target.keyframe_insert("location", frame=f0)
    # 区間の最後は次のショットへ半分だけ近づいておく（切り替え時のジャンプを小さく）
    cam.location = Vector(cp).lerp(Vector(nxt[0]), 0.35)
    cam.keyframe_insert("location", frame=f1 - 1)
    target.location = Vector(tp).lerp(Vector(nxt[1]), 0.35)
    target.keyframe_insert("location", frame=f1 - 1)

# 動きの割り当て。区間の切り替え＝カメラの切り替えなので、動きもそこで切り替える（つなぎ目は見えない）
def play(arm, plan):
    """plan: [(動き名, 開始フレーム, 終了フレーム), ...] を NLA の1トラックに並べる"""
    ad = arm.animation_data_create()
    ad.action = None
    tr = ad.nla_tracks.new()
    for name, f0, f1 in plan:
        act = load_action(name)
        st = tr.strips.new(name, int(f0), act)
        if hasattr(st, "action_slot") and len(getattr(act, "slots", [])):
            st.action_slot = act.slots[0]
        a0, a1 = act.frame_range
        st.repeat = max(0.1, (f1 - f0) / (a1 - a0))
        st.extrapolation = "HOLD_FORWARD"


bounds = [fr(sg["start"]) for sg in segs] + [FRAMES + 1]
play(hero, [(sg.get("hero", "idle"), bounds[i], bounds[i + 1]) for i, sg in enumerate(segs)])
play(staff1, [("idle", 1, FRAMES + 1)])
play(staff2, [("talk", 1, FRAMES + 1)])
play(cust1, [("talk", 1, FRAMES + 1)])
play(wait1, [("sit", 1, FRAMES + 1)])

# 歩く区間は root を窓口まで動かす（歩きは In Place なので前進はこちらで付ける）
WALK_FROM = (0.0, -2.0, 0)
for i, sg in enumerate(segs):
    if sg.get("hero") == "walk":
        hero_root.location = WALK_FROM
        hero_root.keyframe_insert("location", frame=bounds[i])
        hero_root.location = HERO_AT
        hero_root.keyframe_insert("location", frame=bounds[i + 1] - 1)

for o in (cam, target):
    if o.animation_data and o.animation_data.action:
        act = o.animation_data.action
        curves = getattr(act, "fcurves", None)
        if curves is None:  # Blender 5 のレイヤー化アクション
            curves = [c for layer in act.layers for strip in layer.strips
                      for bag in strip.channelbags for c in bag.fcurves]
        for c in curves:
            for kp in c.keyframe_points:
                kp.interpolation = "BEZIER"

if preview:
    # 区間ごとの代表コマだけ静止画で書き出す（見た目の確認用）
    for i, seg in enumerate(segs):
        scene.frame_set(fr((seg["start"] + seg["end"]) / 2))
        scene.render.filepath = str(out_dir / f"preview_{i}.png")
        bpy.ops.render.render(write_still=True)
else:
    bpy.ops.render.render(animation=True)
