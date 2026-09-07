#!/usr/bin/env python3
"""TikTok Studio アップロード画面の編集・予約操作（post_tiktok.py から利用）

2026-07-15 に実地で通した手順をそのままコード化。
各関数は「成功=True / 失敗=False」を返し、失敗しても例外で落とさず呼び出し側に返す
（半自動運用なので、自動化が転けても人間が画面で続行できる方針）。

TikTokのUIは変わりやすい。壊れたら screenshot を撮って各関数のセレクタ／座標判定を直す。
"""
from __future__ import annotations

import time

# --- 運用デフォルト（memory: posting-defaults）---
DEFAULT_BGM_NAME = "Ghibli-like piano solo ballad"  # お気に入りに登録済みの楽曲
DEFAULT_BGM_VOLUME_DB = -20                          # dB（範囲 -60〜+20、既定0は大きい）
DEFAULT_SCHEDULE_TIME = "06:30"                      # 翌日のこの時刻に予約

# TikTokは楽曲名をUIの言語に合わせて出し分ける。同じ曲でも英訳名だったり原題だったりする。
# 2026-08-20を境に編集画面の表示が原題（日本語）へ変わり、英語名しか探していなかった
# add_bgm が楽曲行を見つけられず、**15本連続でBGM無しのまま公開**されていた。
# 同じ曲の別表記をまとめて探す。新しい曲を使う時はここに1行足す。
BGM_ALIASES = (
    ("Ghibli-like piano solo ballad", "ジブリっぽいピアノソロのバラード"),
)


def bgm_aliases(name: str) -> list[str]:
    """指定の曲名と同じ曲を指す別表記を、指定名を先頭にして返す。"""
    for group in BGM_ALIASES:
        if name in group:
            return [name, *(n for n in group if n != name)]
    return [name]


def _click_text(page, text: str, timeout_ms: int = 8000) -> bool:
    """可視要素で完全一致テキストをクリック。見つからなければ False。

    TikTok Studio のDOMは非常に大きい。要素ごとに ``inner_text`` / ``is_visible`` を
    呼ぶと、そのたびにCDPを往復するうえ、内側の全件走査中は期限を確認できない。
    DOMの走査とクリックをブラウザ内のJavaScript 1回で済ませ、待機上限を実効的にする。
    """
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        try:
            clicked = page.evaluate(
                """(wanted) => {
                  const visible = (e) => {
                    const r=e.getBoundingClientRect(), s=getComputedStyle(e);
                    return r.width>0 && r.height>0 && s.visibility!=='hidden'
                      && s.display!=='none' && s.pointerEvents!=='none';
                  };
                  const candidates=[...document.querySelectorAll(
                    'button,[role="button"],label,div,span')]
                    .filter(e => (e.innerText||'').trim()===wanted && visible(e));
                  if(!candidates.length) return false;
                  candidates.sort((a,b) => {
                    const rank=(e) => e.tagName==='BUTTON' ? 0
                      : e.getAttribute('role')==='button' ? 1
                      : e.tagName==='LABEL' ? 2 : 3;
                    return rank(a)-rank(b);
                  });
                  candidates[0].click();
                  return true;
                }""",
                text,
            )
            if clicked:
                return True
        except Exception:
            # UIの再描画中はDOMコンテキストが破棄されることがある。期限内だけ再試行する。
            pass
        time.sleep(0.3)
    return False


def _bgm_on_timeline(page, names: list[str]) -> bool:
    """タイムライン（画面下部）にBGMトラックが乗っているか。

    閾値はウィンドウ高さ相対（下から約25%より下）で判定する。固定px(以前の800)は
    ウィンドウサイズで外れるため（実測でトラックは y≈772 に出て y>800 だと取りこぼした）。
    names は同じ曲の別表記（英訳名/原題）。どれかが乗っていれば成功とみなす。
    """
    try:
        return page.evaluate(
            """(nms) => { const th = window.innerHeight * 0.72;
                 return [...document.querySelectorAll('*')].some(e =>
                   nms.some(nm => (e.textContent||'').includes(nm))
                   && e.getBoundingClientRect().y > th); }""",
            names,
        )
    except Exception:
        return False


def _favorite_track_titles(page) -> list[str]:
    """楽曲パネルに並んでいる曲名を拾う（失敗時に何が表示されているか残すため）。

    曲名が変わったのか、パネル自体が開いていないのかをログだけで切り分けられるようにする。
    """
    try:
        return page.evaluate(
            """() => [...document.querySelectorAll('*')]
                 .filter(e => e.children.length===0 && (e.textContent||'').trim().length>3
                   && (() => { const r=e.getBoundingClientRect();
                        return r.width>60 && r.height>0 && r.y>120 && r.y<window.innerHeight*0.72; })())
                 .map(e => e.textContent.trim()).slice(0, 25)"""
        )
    except Exception:
        return []


def add_bgm(page, name: str = DEFAULT_BGM_NAME) -> bool:
    """編集画面を開き、楽曲パネルのお気に入りから指定曲をタイムラインに追加する。

    曲名は英訳名と原題のどちらで表示されるかがTikTok側の都合で変わるので、
    ``bgm_aliases`` の別表記すべてで探す。
    """
    names = bgm_aliases(name)
    if not _click_text(page, "編集"):
        print("  [BGM] 「編集」ボタンが見つからない")
        return False
    time.sleep(3.5)
    if not _click_text(page, "楽曲"):
        print("  [BGM] 「楽曲」ボタンが見つからない")
        return False
    time.sleep(2.5)
    _click_text(page, "お気に入り")  # タブ。無くても続行
    time.sleep(2.5)

    # トラック行にhoverして赤い「＋」追加ボタンの座標を得てクリック
    for attempt in range(3):
        coords = page.evaluate(
            """(nms) => {
              const title=[...document.querySelectorAll('*')].find(e =>
                e.children.length===0
                && nms.some(nm => (e.textContent||'').trim().startsWith(nm)));
              if(!title) return {err:'no-title'};
              const tr=title.getBoundingClientRect();
              // 行コンテナ
              let row=title;
              for(let i=0;i<6;i++){ if(row.parentElement){ row=row.parentElement;
                const r=row.getBoundingClientRect(); if(r.width>250 && r.height>40 && r.height<130) break; } }
              // 赤系背景の小さい円ボタン（＝＋追加）を探す
              let best=null;
              for(const e of row.querySelectorAll('*')){
                const r=e.getBoundingClientRect(); const s=getComputedStyle(e);
                const m=(s.backgroundColor||'').match(/\\d+/g);
                const red=m && +m[0]>180 && +m[1]<120 && +m[2]<120;
                if(red && r.width>=18 && r.width<=54 && r.height>=18 && r.height<=54){
                  if(!best || r.x>best.x0) best={x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2), x0:r.x};
                }
              }
              return best ? {x:best.x, y:best.y}
                          : {fallbackX:Math.round(tr.x+tr.width+28), fallbackY:Math.round(tr.y+tr.height/2)};
            }""",
            names,
        )
        if coords.get("err"):
            print(f"  [BGM] 楽曲行が見つからない（探した名前: {' / '.join(names)}）")
            time.sleep(1.0)
            continue
        x = coords.get("x", coords.get("fallbackX"))
        y = coords.get("y", coords.get("fallbackY"))
        page.mouse.move(x, y)
        time.sleep(0.3)
        page.mouse.click(x, y)
        time.sleep(3)
        if _bgm_on_timeline(page, names):
            print(f"  [BGM] 追加成功: {name}")
            return True
    titles = _favorite_track_titles(page)
    if titles:
        print(f"  [BGM] 楽曲パネルに見えている名前: {titles}")
    print("  [BGM] タイムラインへの追加を確認できず")
    return False


def set_bgm_volume(page, db: int = DEFAULT_BGM_VOLUME_DB) -> bool:
    """オーディオ設定の音量(dB)入力に値をセット。トラック選択中に呼ぶこと。"""
    handle = page.evaluate_handle(
        """() => {
          const wraps=[...document.querySelectorAll('.PropSettingSliderInput__numberInput')];
          for(const w of wraps){ const a=w.querySelector('.PropSettingInput__additionalText');
            if(a && a.textContent.trim()==='dB') return w.querySelector('input'); }
          return null;
        }"""
    )
    el = handle.as_element()
    if not el:
        print("  [音量] dB入力欄が見つからない")
        return False
    try:
        # 別のTikTokモーダルが重なった時に既定の30秒待ちで処理全体を落とさない。
        # 背面を force click すると誤操作になるため、短時間で安全に失敗扱いへ戻す。
        el.click(timeout=5000)
    except Exception:
        print("  [音量] 別のダイアログに遮られたため設定をスキップ")
        return False
    time.sleep(0.3)
    page.keyboard.press("Meta+A")
    page.keyboard.press("Delete")
    page.keyboard.type(str(db), delay=60)
    time.sleep(0.3)
    page.keyboard.press("Enter")
    time.sleep(0.6)
    val = page.evaluate(
        """() => { const w=[...document.querySelectorAll('.PropSettingSliderInput__numberInput')]
             .find(w=>{const a=w.querySelector('.PropSettingInput__additionalText'); return a&&a.textContent.trim()==='dB';});
             return w ? w.querySelector('input').value : null; }"""
    )
    ok = str(val) == str(db)
    print(f"  [音量] {'設定成功' if ok else '設定失敗'}: {val} dB")
    return ok


def save_editor(page) -> bool:
    """編集画面の「保存」を押して投稿ページに戻る。"""
    if not _click_text(page, "保存"):
        print("  [保存] 保存ボタンが見つからない")
        return False
    # オーディオ見出しが消える＝編集画面を抜けた、で判定
    for _ in range(20):
        time.sleep(1)
        try:
            if not page.query_selector("text=オーディオ"):
                print("  [保存] 編集を保存し投稿ページへ戻りました")
                return True
        except Exception:
            pass
    print("  [保存] 戻りを確認できず（続行）")
    return True


def _set_time_picker(page, hhmm: str) -> bool:
    """時刻ピッカーを開いて HH:MM を選択。仮想スクロール対策に scrollIntoView を使う。"""
    tfield = None
    for inp in page.query_selector_all("input[type=text]"):
        try:
            v = inp.input_value()
        except Exception:
            continue
        if v and ":" in v and len(v) <= 6:
            tfield = inp
            break
    if not tfield:
        print("  [予約] 時刻入力が見つからない")
        return False
    hh, mm = hhmm.split(":")
    try:
        tfield.scroll_into_view_if_needed()
        tfield.click(timeout=5000)
    except Exception:
        # 日付ピッカー等が残っていても30秒のclick timeoutを外へ漏らさない。
        page.keyboard.press("Escape")
        print("  [予約] 時刻入力が別のダイアログに遮られています")
        return False
    time.sleep(1)
    for val, is_hour in ((hh, True), (mm, False)):
        coords = page.evaluate(
            """(a) => {
              const [val,isHour]=a;
              const items=[...document.querySelectorAll('div,span,li')].filter(e =>
                e.children.length===0 && e.textContent.trim()===val);
              for(const e of items){ const r=e.getBoundingClientRect();
                const inCol = isHour ? r.x<350 : r.x>=350;
                if(inCol && r.width>0){ e.scrollIntoView({block:'center'}); const rr=e.getBoundingClientRect();
                  return {x:Math.round(rr.x+rr.width/2), y:Math.round(rr.y+rr.height/2)}; } }
              return null;
            }""",
            [val, is_hour],
        )
        if coords:
            time.sleep(0.4)
            page.mouse.click(coords["x"], coords["y"])
            time.sleep(0.7)
    page.keyboard.press("Escape")
    time.sleep(0.3)
    got = tfield.input_value()
    ok = got == hhmm
    print(f"  [予約] 時刻 {'設定成功' if ok else '不一致'}: {got}")
    return ok


def _find_date_field(page):
    for inp in page.query_selector_all("input[type=text]"):
        try:
            v = inp.input_value()
        except Exception:
            continue
        if v and v.count("-") == 2:
            return inp
    return None


def _click_calendar_day(page, day: int) -> bool:
    """カレンダーの日セルのうち「有効（不透明・非白＝過去日でも今日でもない）」を1つクリック。

    日セルは幅~32px。時刻ピッカーの数字(幅~80px)や曜日ラベルと混ざらないよう幅で絞る。
    有効判定は色: 有効=rgb(22,24,35)不透明 / 過去=rgba(...,0.34)半透明 / 今日=白 で見分ける。
    """
    coords = page.evaluate(
        r"""(d) => {
          const cells=[...document.querySelectorAll('*')].filter(e =>
            e.children.length===0 && e.textContent.trim()===d);
          for(const c of cells){ const r=c.getBoundingClientRect(); const s=getComputedStyle(c);
            if(!(r.width>=20 && r.width<=50 && r.y>250)) continue;   // カレンダー日セルのみ
            const m=(s.color||'').match(/[\d.]+/g); if(!m) continue;
            const opaque = m.length<4 || parseFloat(m[3])>=1;         // 半透明=過去日は除外
            const notWhite = (+m[0] + +m[1] + +m[2]) < 600;           // 白=今日は除外
            if(opaque && notWhite) return {x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)};
          }
          return null;
        }""",
        str(day),
    )
    if not coords:
        return False
    page.mouse.click(coords["x"], coords["y"])
    time.sleep(0.6)
    return True


def _click_next_month(page) -> None:
    """カレンダー上部の右矢印（日グリッドより上の小要素で最も右）を押す。"""
    page.evaluate(
        r"""() => {
          const days=[...document.querySelectorAll('*')].filter(e=>e.children.length===0
            && /^[0-9]{1,2}$/.test((e.textContent||'').trim())
            && (()=>{const r=e.getBoundingClientRect(); return r.width>=20&&r.width<=50&&r.y>250;})());
          if(!days.length) return;
          const top=Math.min(...days.map(e=>e.getBoundingClientRect().y));
          const cand=[...document.querySelectorAll('button,svg,div,span')].filter(e=>{
            const r=e.getBoundingClientRect();
            return r.width>0 && r.width<44 && r.height>0 && r.height<44 && r.y<top-8 && r.y>top-90;});
          cand.sort((a,b)=>a.getBoundingClientRect().x-b.getBoundingClientRect().x);
          const nxt=cand[cand.length-1];
          if(nxt){
            // svg等 click() を持たない要素があるので、無ければ実座標クリックのフォールバック
            if(typeof nxt.click==='function'){ nxt.click(); }
            else{ const r=nxt.getBoundingClientRect();
              const t=document.elementFromPoint(r.x+r.width/2, r.y+r.height/2);
              if(t && typeof t.click==='function') t.click(); }
          }
        }"""
    )


def set_date(page, date_str: str) -> bool:
    """予約日付をカレンダーで選ぶ。date_str='YYYY-MM-DD'。

    TikTokの既定日付は「今日」のことがあり翌日に自動でならないため明示選択する。
    まず現在表示の月で目的日をクリック、無ければ翌月に進んで再試行（翌日が翌月の場合に対応）。
    ヘッダ文字の解析には依存しない。
    """
    d = int(date_str.split("-")[2])
    dfield = _find_date_field(page)
    if not dfield:
        print("  [予約] 日付入力が見つからない")
        return False
    try:
        dfield.scroll_into_view_if_needed()
        dfield.click(timeout=5000)
    except Exception:
        # 開き残ったピッカー/モーダルを閉じ、背面への強制クリックはしない。
        page.keyboard.press("Escape")
        print("  [予約] 日付入力が別のダイアログに遮られています")
        return False
    time.sleep(1.2)

    for attempt in range(4):
        if _click_calendar_day(page, d):
            # クリック直後は input_value がまだ更新されない競合があるため、少し待って再確認する
            for _ in range(6):
                if dfield.input_value() == date_str:
                    # TikTokのカレンダーは値更新後もoverlayが残る場合がある。
                    # 次の時刻欄を遮らないよう明示的に閉じる。
                    page.keyboard.press("Escape")
                    print(f"  [予約] 日付 設定成功: {date_str}")
                    return True
                time.sleep(0.3)
        _click_next_month(page)   # 見つからない=翌月にある可能性 → 進める
        time.sleep(0.7)

    # 失敗時にカレンダーの TUXModal-overlay を残すと、続く時刻欄への
    # ElementHandle.click が背面要素扱いになり30秒で例外終了する。
    page.keyboard.press("Escape")
    print(f"  [予約] 日付 設定失敗（現在 {dfield.input_value()} / 目標 {date_str}）")
    return False


def set_schedule(page, date_str: str, time_str: str = DEFAULT_SCHEDULE_TIME) -> bool:
    """投稿予約する を選択し、時刻を設定。日付は既定で翌日になっていれば流用、違えば警告。

    date_str: 'YYYY-MM-DD'（想定する予約日＝翌日）
    """
    # 「投稿予約する」ラジオを選択
    radio = page.query_selector("input[value='schedule']")
    if radio:
        try:
            radio.click()
        except Exception:
            _click_text(page, "投稿予約する")
    else:
        _click_text(page, "投稿予約する")
    time.sleep(1.5)

    date_ok = set_date(page, date_str)
    time_ok = _set_time_picker(page, time_str)
    return time_ok and date_ok
