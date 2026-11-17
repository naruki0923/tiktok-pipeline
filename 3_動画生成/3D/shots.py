"""もしも型3D動画の「ショットの型」と、LLMの台本JSON → make_3d.py 用 spec への変換。

LLM には座標を書かせない。カメラ・人物の置き場所・動きは、1本目（国保もしも）で
見た目を確かめた値をここに固定し、LLM は型の名前（"home_sofa" など）を選ぶだけにする。
自由に座標を書かせると、人物が画面から外れる・家具に埋まる等が毎回起きて、
無人では誰も気づけないため。

標準ライブラリだけで動く（2_台本生成 の venv からも import する）。

    from shots import build_spec, SHOT_GUIDE
    spec, errors, warnings = build_spec(llm_json, name="本番_150")
"""
from __future__ import annotations

import copy
import re
import unicodedata

# ------------------------------------------------------------------ 固定の前提
START_MONEY = 120                 # カウンター「手元のお金」の初期値（万円）。毎回同じ人の話にする
ASSUMPTION = "貯金120万円・前の年の年収400万円・会社員を辞めた40代"   # LLMに渡す前提
NOTE = "※数字はイメージです"       # カウンターが出ている間ずっと出す
IDEAL_SUB = "※もしもの話です"      # もしもの世界の小字幕は固定
REWIND_SAY = "でも現実は違います"
REWIND_BIG = "でも、現実は違う"
CTA_SAY = ["申請方法を詳しく知りたい人は",
           "このアカウントをフォローして",
           "無料の個別面談に参加してみてください"]

# 書かせない言い回し（もしも型では入れないと社長が決めた。2026-11-16）
BANNED = [
    (re.compile(r"いいね|保存して|二度と.{0,8}表示されない|おすすめには表示"), "いいね保存の誘導"),
    # 「毎朝ゆっくり起きられる」のような描写は弾かない。投稿の時間を告げる言い回しだけ
    (re.compile(r"毎朝.{0,12}(この時間|投稿|配信|お届け|更新)|お金の話は.{0,6}毎朝"), "中盤フォローの誘導"),
    (re.compile(r"必ずもらえ|絶対に?もらえ|誰でももらえ|100%"), "断定・誇大表現"),
]

MAX_BIG, MAX_SUB, MAX_TITLE_LINE, MAX_PROP = 18, 22, 14, 10
# 半角の . , は数字の間（1.5倍・1,000）では区切らない
_PUNCT = re.compile(r"(?:[。、，．！？!?]|(?<!\d)[.,]|[.,](?!\d))+")
MAX_SAY_LINE = 30


# ------------------------------------------------------------------ ショットの型
# 値は 試作_3D/spec_kokuho.json で書き出して確かめたもの。座標はその場所の原点からの相対。
# mood: "ideal"（もしもの世界）/ "real"（現実）/ "any"。同じ名前で mood ごとに中身を変えられる。
_SOFA = {"act": "sit", "at": [-1.3, 3.25, 0.06], "rot": 0}
_COUNTER = [0.0, 1.95, 0]

SHOTS = {
    "home_wide": {
        "desc": "自宅を引きで。ソファに座る主人公（冒頭・場面の導入向き）",
        "any": {"scene": "home", "cam": [0.4, -3.4, 2.5], "tgt": [-0.6, 2.8, 1.0],
                "cam2": [0.2, -2.6, 2.2], "hero": dict(_SOFA)},
    },
    "home_sofa": {
        "desc": "ソファに座る主人公の寄り（くつろぐ／考え込む）",
        "any": {"scene": "home", "cam": [-0.5, 0.2, 1.6], "tgt": [-1.3, 3.2, 0.9],
                "hero": {**_SOFA, "offset": 0.3}},
        "real": {"scene": "home", "cam": [-0.5, 0.2, 1.6], "tgt": [-1.3, 3.2, 0.9],
                 "hero": dict(_SOFA), "show": ["letter_real"]},
    },
    "home_letter": {
        "desc": "テーブルの上の書類のアップ（請求書・通知が届いた場面）",
        "ideal": {"scene": "home", "cam": [-0.7, 1.05, 1.3], "tgt": [-1.15, 2.1, 0.43],
                  "cam2": [-0.85, 1.3, 1.05],
                  "hero": {"act": "think", "at": [-0.2, 2.6, 0], "rot": 120}, "show": ["letter_ideal"]},
        "real": {"scene": "home", "cam": [-0.7, 1.05, 1.3], "tgt": [-1.15, 2.1, 0.43],
                 "cam2": [-0.85, 1.3, 1.05],
                 "hero": {"act": "think", "at": [-0.2, 2.6, 0], "rot": 120}, "show": ["letter_real"]},
    },
    "home_desk": {
        "desc": "机のノートPCで求人を見る（もしも＝じっくり選ぶ／現実＝焦って探す）",
        "ideal": {"scene": "home", "cam": [1.5, 1.6, 1.75], "tgt": [2.5, 3.85, 0.95],
                  "cam2": [1.75, 2.1, 1.6],
                  "hero": {"act": "sit", "at": [2.5, 3.0, 0.03], "rot": 180}, "show": ["pc_ideal"]},
        "real": {"scene": "home", "cam": [1.3, 0.8, 1.85], "tgt": [2.0, 3.6, 1.1],
                 "cam2": [1.45, 1.25, 1.75],
                 "hero": {"act": "look", "at": [1.55, 3.15, 0], "rot": 150}, "show": ["pc_real"]},
    },
    "home_worry": {
        "desc": "書類を前に頭を抱える（現実だけ）",
        "real": {"scene": "home", "cam": [0.5, -1.4, 1.65], "tgt": [-0.3, 1.8, 1.05],
                 "cam2": [0.35, -0.8, 1.6],
                 "hero": {"act": "head", "at": [-0.3, 1.5, 0], "rot": 0, "offset": 0.25},
                 "show": ["letter_real"]},
    },
    "home_sad": {
        "desc": "部屋の真ん中で肩を落とす（現実だけ）",
        "real": {"scene": "home", "cam": [0.4, -3.4, 2.5], "tgt": [-0.6, 2.4, 1.0],
                 "cam2": [0.25, -2.8, 2.3],
                 "hero": {"act": "sad", "at": [-0.3, 1.5, 0], "rot": 0}},
    },
    "park_walk": {
        "desc": "平日の公園をのんびり歩く（もしもの世界向き）",
        "any": {"scene": "park", "cam": [1.4, -6.0, 1.7], "tgt": [0.0, -1.0, 1.1],
                "cam2": [1.4, -9.0, 1.7], "tgt2": [0.0, -4.5, 1.1],
                "hero": {"act": "walk", "at": [0.0, 1.0, 0], "rot": 0, "to": [0.0, -5.2, 0]}},
    },
    "park_stand": {
        "desc": "公園で空を見上げて立つ（区切り・前向きな締め）",
        "any": {"scene": "park", "cam": [-2.6, -4.5, 0.7], "tgt": [0.8, 3.0, 2.3],
                "cam2": [-2.2, -3.4, 0.8],
                "hero": {"act": "idle", "at": [0.3, 0.5, 0], "rot": 200}},
    },
    "office_walk": {
        "desc": "役所の窓口へ歩いていく（今の制度パートの1つ目に使う）",
        "any": {"scene": "office", "cam": [0.0, -6.5, 4.6], "tgt": [0.0, 2.6, 1.3],
                "cam2": [0.0, -5.4, 4.2],
                "hero": {"act": "walk", "at": [0.0, -2.2, 0], "rot": 180, "to": list(_COUNTER)}},
    },
    "office_counter": {
        "desc": "窓口の前に立つ主人公を斜めから（制度の中身を言う所）",
        "any": {"scene": "office", "cam": [1.6, 0.6, 2.6], "tgt": [0.2, 2.7, 1.1],
                "cam2": [1.4, 1.0, 2.4],
                "hero": {"act": "idle", "at": list(_COUNTER), "rot": 180}},
    },
    "office_talk": {
        "desc": "窓口で職員と話す（届け出・申請の場面）",
        "any": {"scene": "office", "cam": [1.6, -3.6, 3.6], "tgt": [0.0, 2.6, 1.0],
                "cam2": [1.2, -2.8, 3.2],
                "hero": {"act": "talk", "at": list(_COUNTER), "rot": 180}},
    },
    "office_think": {
        "desc": "窓口で考え込む（期限・条件・注意点）",
        "any": {"scene": "office", "cam": [-2.2, 0.2, 2.4], "tgt": [0.0, 2.3, 1.3],
                "cam2": [-1.9, 0.6, 2.3],
                "hero": {"act": "think", "at": list(_COUNTER), "rot": 180}},
    },
}

# パートごとに使ってよい型
PART_SHOTS = {
    "hook": ["home_wide", "home_sofa", "park_stand"],
    "ideal": ["home_sofa", "home_letter", "home_desk", "park_walk", "park_stand"],
    "real": ["home_sofa", "home_letter", "home_worry", "home_sad", "home_desk"],
    "system": ["office_walk", "office_counter", "office_talk", "office_think"],
}
PART_ORDER = ["hook", "ideal", "real", "system"]
PART_MIN = {"hook": 1, "ideal": 4, "real": 3, "system": 3}
PART_MAX = {"hook": 1, "ideal": 7, "real": 6, "system": 6}

_CTA_CAMS = [([0.0, -6.5, 4.6], [0.0, -5.6, 4.3]), ([0.0, -5.6, 4.3], [0.0, -5.0, 4.0]),
             ([0.0, -5.0, 4.0], [0.0, -4.6, 3.8])]

# 3Dの小物に書く文字（場面の中の看板・書類）。LLMが話題に合わせて差し替える
DEFAULT_PROPS = {
    "letter_ideal": ["保険料のお知らせ", "今月の請求　0"],
    "letter_real": ["納付通知書", "国民健康保険料"],
    "pc_ideal": ["求人　じっくり比較中", "やりたい仕事を探す"],
    "pc_real": ["急募！即日勤務OK", "未経験歓迎"],
    "office_sign": "市民窓口",
    "office_window": "国民健康保険",
    "office_notice": ["納付通知書", "国民健康保険料"],
    "office_doc": "受給資格者証",
}


def shot_guide() -> str:
    """プロンプトに載せる「選べる型」の一覧。"""
    out = []
    for part in PART_ORDER:
        out.append(f"- {part}: " + " / ".join(f"`{n}`（{SHOTS[n]['desc']}）" for n in PART_SHOTS[part]))
    return "\n".join(out)


def _shot(name: str, mood: str) -> dict:
    s = SHOTS[name]
    return copy.deepcopy(s.get(mood) or s.get("any") or s.get("real") or s.get("ideal"))


def _clean(s) -> str:
    return str(s or "").strip()


def _props(raw: dict | None, errors: list[str]) -> dict:
    """小物の文字。長すぎる・型が違う所は既定に戻して警告ではなくエラーにする。"""
    out = copy.deepcopy(DEFAULT_PROPS)
    if raw is not None and not isinstance(raw, dict):
        errors.append("props は {\"letter_ideal\": [..], …} の形にしてください")
        return out
    for k, v in (raw or {}).items():
        if k not in out:
            continue
        want_list = isinstance(out[k], list)
        vals = v if isinstance(v, list) else [v]
        vals = [_clean(x) for x in vals][: (2 if want_list else 1)]
        if not all(vals) or (want_list and len(vals) != 2):
            errors.append(f"props.{k} は{'2行' if want_list else '1つ'}の文字で書いてください")
            continue
        if any(len(x) > MAX_PROP for x in vals):
            errors.append(f"props.{k} が長すぎます（1行{MAX_PROP}字まで）: {vals}")
            continue
        out[k] = vals if want_list else vals[0]
    return out


def _banned(text: str) -> str | None:
    for rx, why in BANNED:
        m = rx.search(text)
        if m:
            return f"{why}（「{m.group(0)}」）"
    return None


def build_spec(src: dict, name: str) -> tuple[dict, list[str], list[str]]:
    """LLMの台本JSON → make_3d.py 用 spec。(spec, エラー, 警告) を返す。

    エラーが1つでもあれば spec は使わないこと（呼び出し側がLLMに直させる）。
    巻き戻し（でも現実は違います）と最後のCTA3行はここで足す。LLMには書かせない。
    """
    errors: list[str] = []
    warns: list[str] = []
    raw = src.get("segments") or []
    if not isinstance(raw, list) or not raw:
        return {}, ["segments がありません"], []
    if not all(isinstance(x, dict) for x in raw):
        return {}, ["segments の中身は1区間ずつ {\"part\": …, \"say\": [...]} の形にしてください"], []

    title = _clean(src.get("title_band")).replace("\\n", "\n")
    tl = [x for x in title.split("\n") if x.strip()]
    if not (1 <= len(tl) <= 2) or any(len(x) > MAX_TITLE_LINE for x in tl):
        errors.append(f"title_band は「\\n」で区切った2行・1行{MAX_TITLE_LINE}字までにしてください: {title!r}")
    if not title.startswith("もし"):
        errors.append("title_band は「もし」で始めてください")

    # パートの並び（hook → ideal → real → system）と本数
    parts = [_clean(s.get("part")) for s in raw]
    order = [p for k, p in enumerate(parts) if k == 0 or parts[k - 1] != p]
    if order != PART_ORDER:
        errors.append(f"パートの並びは hook→ideal→real→system の1回ずつにしてください（今: {'→'.join(order)}）")
    for p in PART_ORDER:
        n = parts.count(p)
        if not PART_MIN[p] <= n <= PART_MAX[p]:
            errors.append(f"{p} の区間は{PART_MIN[p]}〜{PART_MAX[p]}個にしてください（今{n}個）")

    segs: list[dict] = []
    first_ideal: dict | None = None
    last = {"ideal": (0, START_MONEY), "real": (0, START_MONEY)}
    for k, s in enumerate(raw):
        part = parts[k]
        # say は「1行＝読み上げの1フレーズ」。句読点が入っていたらそこで行を分ける
        # （行末の「。」は台本が「。。」になり、行中の「、」はフックが4行以上に割れて
        # 旧形式での作り直しが title_problems で止まるため）
        lines = s.get("say") or []
        if isinstance(lines, str):          # 1行だけを文字列で返してきた時（1文字ずつに割らない）
            lines = [lines]
        say = [x.strip() for ln in lines for x in _PUNCT.split(_clean(ln)) if x.strip()]
        where = f"{k + 1}番目の区間（{part}）"
        if not say:
            errors.append(f"{where}: say が空です")
            continue
        for ln in say:
            if len(ln) > MAX_SAY_LINE:
                errors.append(f"{where}: say の1行が長すぎます（{MAX_SAY_LINE}字まで）: {ln}")
        shot = _clean(s.get("shot"))
        if part in PART_SHOTS and shot not in PART_SHOTS[part]:
            errors.append(f"{where}: shot `{shot}` は使えません。使えるのは {PART_SHOTS[part]}")
            continue
        if part not in PART_SHOTS:
            continue
        mood = {"ideal": "ideal", "real": "real"}.get(part, "any")
        seg = _shot(shot, mood)
        big = _clean(s.get("big"))
        sub = _clean(s.get("sub"))
        if not big:
            errors.append(f"{where}: big（大字幕）が空です")
        if len(big) > MAX_BIG:
            errors.append(f"{where}: big が長すぎます（{MAX_BIG}字まで）: {big}")
        if len(sub) > MAX_SUB:
            errors.append(f"{where}: sub が長すぎます（{MAX_SUB}字まで）: {sub}")
        seg.update({"say": say, "part": part, "big": big, "sub": sub})
        if s.get("join") and part != "hook":
            seg["join"] = True
        if part == "hook":
            seg.update({"grade": "normal", "badge": None, "day": None, "sub": IDEAL_SUB})
            if len(say) not in (2, 3):
                errors.append(f"{where}: フックは2〜3行（1文）にしてください（今{len(say)}行）")
            if not re.search(r"[0-9０-９]|[一二三四五六七八九十百千]+[歳年か月日割]", "".join(say) + title):
                warns.append("フック（タイトル帯か1行目）に年齢・期限・日数などの数字がありません")
        elif part in ("ideal", "real"):
            day, money = s.get("day"), s.get("money")
            if not isinstance(day, int) or not 1 <= day <= 730:
                errors.append(f"{where}: day（退職してから何日目）は1〜730の整数にしてください: {day!r}")
                day = 1
            if not isinstance(money, int) or not 0 <= money <= START_MONEY:
                errors.append(f"{where}: money（手元のお金・万円）は0〜{START_MONEY}の整数にしてください: {money!r}")
                money = START_MONEY
            pd, pm = last[part]
            if day < pd:
                errors.append(f"{where}: day が前の区間より戻っています（{pd}→{day}）")
            if money > pm:
                errors.append(f"{where}: money が前の区間より増えています（{pm}→{money}）。"
                              f"手元のお金は{START_MONEY}万円から減るだけにしてください")
            last[part] = (day, money)
            seg.update({"day": day, "money": money, "grade": part,
                        "badge": "もしもの世界" if part == "ideal" else "現実"})
            if part == "ideal":
                seg["sub"] = IDEAL_SUB
                first_ideal = first_ideal or seg
            elif not sub:
                seg["sub"] = NOTE
        else:   # system
            seg.update({"grade": "normal", "badge": "今の制度", "day": None})
        # 文の終わりにならない join は次の区間の頭と1文になる。最後の区間は必ず文を閉じる
        segs.append(seg)

    # パートの最後は必ず文を閉じる（巻き戻し・CTAの前で文がつながらないように）
    for k, seg in enumerate(segs):
        nxt = segs[k + 1] if k + 1 < len(segs) else None
        if seg.get("join") and (nxt is None or nxt["part"] != seg["part"]):
            seg.pop("join")

    allText = "\n".join([title, _clean(src.get("title")), *(x for s in segs for x in s["say"]),
                         *(s["big"] + " " + s["sub"] for s in segs)])
    hit = _banned(unicodedata.normalize("NFKC", allText))   # 全角の「１００％」も拾う
    if hit:
        errors.append(f"書かない言い回しが入っています: {hit}")
    if re.search(r"フォロー|個別面談|個別相談", "\n".join(x for s in segs for x in s["say"])):
        errors.append("CTA（フォロー・個別面談）は書かないでください。最後に固定文言を自動で足します")
    if (s := next((s for s in segs if s["part"] == "ideal"), None)) and s["day"] > 7:
        warns.append(f"もしもの世界の最初の日付が{s['day']}日目です（1日目から始めると流れが分かりやすい）")

    props = _props(src.get("props"), errors)
    if errors:
        return {}, errors, warns

    # 巻き戻し（もしもの最後の画を逆再生して1日目へ戻す。3Dは書き出さない）
    ideal_end = max(k for k, s in enumerate(segs) if s["part"] == "ideal")
    rewind = _shot("home_wide", "any")
    rewind["hero"] = {"act": "idle", "at": [-0.2, 1.6, 0], "rot": 0}
    rewind.update({"say": [REWIND_SAY], "part": "rewind", "pad": 0.9, "rewind": True, "render": False,
                   "grade": "rewind", "badge": "巻き戻し", "day": first_ideal["day"],
                   "money": START_MONEY, "big": REWIND_BIG, "sub": ""})
    segs.insert(ideal_end + 1, rewind)

    # CTA（固定3行・役所を引きで）
    for k, line in enumerate(CTA_SAY):
        c, c2 = _CTA_CAMS[k]
        segs.append({"scene": "office", "cam": c, "tgt": [0.0, 2.6, 1.3], "cam2": c2,
                     "hero": {"act": "idle", "at": list(_COUNTER), "rot": 180},
                     "say": [line], "part": "cta", "grade": "normal", "badge": None, "day": None,
                     "big": line if k < 2 else "", "sub": "", **({"join": True} if k < 2 else {"cta": True})})

    spec = {"name": name, "title": "\n".join(tl), "note": NOTE, "props": props,
            "start_money": START_MONEY, "segments": segs}
    return spec, [], warns


# ------------------------------------------------------------------ 台本テキスト
def script_text(spec: dict) -> str:
    """句読点ありの原稿（format_script.py に通す用）。区間の say 行を読点でつなぎ、
    join の無い区間の終わりで句点を打つ。旧形式で作り直す時もこの原稿を使う。"""
    out, buf = [], []
    for seg in spec["segments"]:
        buf.extend(seg["say"])
        if not seg.get("join"):
            out.append("、".join(buf) + "。")
            buf = []
    if buf:
        out.append("、".join(buf) + "。")
    return "\n".join(out) + "\n"


def spoken_lines(spec: dict) -> list[str]:
    return [x for s in spec["segments"] for x in s["say"]]


# ------------------------------------------------------------------ 尺の見積もり
GAP, BLANK_GAP, LEAD, TAIL = 0.12, 0.25, 0.3, 0.6      # make_3d.py の narrate() と同じ間
SPEED = 1.2
CHARS_PER_SEC = 7.6                                      # VOICEVOX が使えない時の目安（国保もしもの実測）


def narration_seconds(spec: dict, line_seconds=None) -> float:
    """ナレーションの長さの見積もり。line_seconds(行) → 秒 を渡せば実測に近い値になる。"""
    total = LEAD + TAIL
    segs = spec["segments"]
    for k, s in enumerate(segs):
        for ln in s["say"]:
            total += (line_seconds(ln) if line_seconds else len(ln) / CHARS_PER_SEC) + GAP
        if k < len(segs) - 1:
            total += (0.0 if s.get("join") else BLANK_GAP) + s.get("pad", 0.0)
    return total


def voicevox_line_seconds(host: str, speaker_id: int):
    """VOICEVOX の audio_query からモーラ長を足して1行の秒数を返す関数を作る（合成はしない）。"""
    import json
    import urllib.parse
    import urllib.request

    def f(text: str) -> float:
        q = urllib.parse.urlencode({"text": text, "speaker": speaker_id})
        req = urllib.request.Request(f"{host}/audio_query?{q}", data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            aq = json.loads(r.read().decode("utf-8"))
        sec = 0.0
        for ap in aq.get("accent_phrases", []):
            for m in ap.get("moras", []):
                sec += (m.get("consonant_length") or 0) + (m.get("vowel_length") or 0)
            pm = ap.get("pause_mora")
            if pm:
                sec += pm.get("vowel_length") or 0
        return sec / SPEED
    return f
