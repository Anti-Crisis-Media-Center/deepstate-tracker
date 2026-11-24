import re
"""Автотести: «контрольні зразки» з відомою відповіддю.

Запускаються самі на GitHub при кожній зміні коду (вкладка Actions → «Автотести»).
Якщо зміна випадково зламала підрахунок чи назви — тест стане червоним ще до публікації.
"""
import json
import sys
from pathlib import Path

import pytest
from shapely.geometry import box, mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import change_map  # noqa: E402
import tracker  # noqa: E402

# невеликі квадрати поблизу фронту (координати умовні — для площі важлива лише форма)
A = box(37.50, 48.40, 37.52, 48.41)
B = box(37.30, 48.30, 37.32, 48.31)


def write_day(d: Path, day: str, feats: list[tuple]) -> Path:
    p = d / f"{day}.geojson"
    p.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(g), "properties": {"oblast": "donetsk", "change": ch}}
        for g, ch in feats]}))
    return p


def test_occupied_only(tmp_path):
    f = [write_day(tmp_path, "2026-01-01", [(A, "occupied")])]
    b = change_map.breakdown(f)
    assert b["occupied"] > 0 and b["deoccupied"] == 0 and b["flip"] == 0


def test_marked_and_removed_is_not_counted(tmp_path):
    """Позначили й зняли за період — ні в окупованому, ні в деокупованому."""
    f = [write_day(tmp_path, "2026-01-01", [(A, "occupied")]),
         write_day(tmp_path, "2026-01-02", [(A, "deoccupied")])]
    b = change_map.breakdown(f)
    assert b["occupied"] == 0 and b["deoccupied"] == 0 and b["flip"] > 0


def test_partly_removed(tmp_path):
    """Зняли лише частину — решта лишається окупованою."""
    half = box(37.50, 48.40, 37.51, 48.41)
    f = [write_day(tmp_path, "2026-01-01", [(A, "occupied")]),
         write_day(tmp_path, "2026-01-02", [(half, "deoccupied")])]
    b = change_map.breakdown(f)
    full = change_map.breakdown([f[0]])["occupied"]
    assert b["occupied"] == pytest.approx(full / 2, rel=0.05)
    assert b["deoccupied"] == 0


def test_to_grey_is_not_deoccupation(tmp_path):
    """Червоне стало сірою зоною — не рахуємо як деокупацію."""
    f = [write_day(tmp_path, "2026-01-01", [(B, "deoccupied"), (A, "to_grey")])]
    b = change_map.breakdown(f)
    assert b["deoccupied"] > 0 and b["to_grey"] > 0
    assert b["deoccupied"] == pytest.approx(change_map.breakdown([write_day(tmp_path, "x", [(B, "deoccupied")])])["deoccupied"])


def test_compare_splits_grey():
    prev, cur, unknown = box(0, 0, 2, 1), box(0, 0, 1, 1), box(1.5, 0, 2, 1)
    r = tracker.compare(cur, prev, [], "Донецька", unknown)
    assert r["deocc_km2"] > 0 and r["grey_km2"] > 0
    assert r["deocc_km2"] == pytest.approx(r["grey_km2"], rel=0.05)   # по половині


def test_steps_follow_dates(tmp_path):
    """Блимання по черзі: два дні окупації — один крок, потім деокупація — другий."""
    f = [write_day(tmp_path, "2026-01-01", [(A, "occupied")]),
         write_day(tmp_path, "2026-01-02", [(B, "occupied")]),
         write_day(tmp_path, "2026-01-03", [(A, "deoccupied")])]
    assert change_map.step_groups(f) == [1, 2, 3]   # кожен день — окремий крок (16.11.2026)


def test_one_direction_no_steps(tmp_path):
    # 16.11.2026: і за самої окупації кожен день — окремий крок (динаміка); один день — без кроків
    f = [write_day(tmp_path, "2026-01-01", [(A, "occupied")]),
         write_day(tmp_path, "2026-01-02", [(B, "occupied")])]
    assert change_map.step_groups(f) == [1, 2]
    assert change_map.step_groups(f[:1]) == []


REAL_WEEK = [f"2026-11-0{i}" for i in range(3, 10)]


@pytest.mark.skipif(not (tracker.CHANGES_DIR / "2026-11-05.geojson").exists(), reason="немає даних тижня 2–9.11")
def test_names_real_week():
    """Справжній тиждень 3–9.11.2026: сусідні ділянки (<3 км, з 12.11) — одна назва (Добропілля + Білицьке
    за 1,8 км → «Білицьке»); Покровськ за 4,1 км — окрема назва; місто лише ближче 10 км; до 4 назв."""
    assert tracker.place_names(REAL_WEEK) == ["Покровськ", "Білицьке", "Русин Яр", "Костянтинівка"]


def test_names_small_separate_area():
    """12.11.2026: ділянка 0,37 км² за 4,4 км від Костянтинівки — окрема назва (Часів Яр)."""
    assert tracker.place_names(["2026-11-12"]) == ["Іванопілля", "Червоне"]   # щоденна: найближчий пункт (16.11.2026)


def test_names_title():
    assert tracker.names_title(["Покровськ"]) == "Поблизу: Покровськ"
    assert tracker.names_title(["Русин Яр", "Покровськ"]) == "Поблизу: Русин Яр · Покровськ"


def test_net_ignores_grey():
    """Чиста зміна: перехід у сіру зону не зменшує «окуповано»."""
    rows = [{"date": "2026-01-01", "donetsk_occupied_km2": 100, "donetsk_newly_occupied_km2": 0,
             "donetsk_deoccupied_km2": 0, "donetsk_to_grey_km2": 0},
            {"date": "2026-01-02", "donetsk_occupied_km2": 98, "donetsk_newly_occupied_km2": 0,
             "donetsk_deoccupied_km2": 0, "donetsk_to_grey_km2": 2}]
    assert tracker._net_period(rows, "donetsk") == 0


def test_near_phrase_same_names_as_map():
    assert tracker.near_phrase(["Добропілля", "Білицьке"], ["Василівка (Покровський р-н)", "Світле (Покровський р-н)"]) \
        == "поблизу: Добропілля, Білицьке"   # без дужок із селами (12.11.2026)
    assert tracker.near_phrase(["Русин Яр"], ["Русин Яр (Краматорський р-н)"]) == "поблизу: Русин Яр"


def test_names_from_deepstate_messages_day():
    """Пункти з повідомлень DeepState розпізнаються (відмінки: «у Костянтинівці», «Русиного Яру») — для внутрішньої
    звірки. У дописах повідомлень DeepState більше немає (рішення Юлі 18.11.2026, впроваджено 24.11)."""
    pn = list(tracker.place_names(["2026-11-05"]))
    assert tracker.deepstate_mentioned(["2026-11-05"])[:2] == ["Костянтинівка", "Русин Яр"]
    assert "Русин Яр" not in tracker.deepstate_mentioned(["2026-11-05"], skip=pn)   # без повторів головних назв
    r = {k: {"newly_km2": 1.0, "deocc_km2": 0, "grey_km2": 0, "newly_places": [], "deocc_places": [],
             "grey_places": []} for k in tracker.OBLASTS}
    msgs = [{"time": __import__("datetime").datetime(2026, 11, 5, 22, 0), "text": "Ворог просунувся поблизу Русиного Яру."}]
    txt = tracker.build_text("05.11.2026", r, None, msgs, {"donetsk": pn}, {"donetsk": ["Русин Яр"]})
    assert "Також у повідомленнях" not in txt and "Повідомлення на сайті" not in txt
    assert "(" not in re.sub(r"\([^()]* громада\)", "", txt)   # дужки лише для громад (16.11)


def test_day_two_areas_get_two_names_one_hromada():
    """18.11.2026: дві ділянки за 1,6 км — кожна зі своєю назвою; села однієї громади — через «й», громада один раз."""
    import tracker as T
    n = T.place_names(["2026-11-18"], "donetsk")
    assert list(n) == ["Василівка", "Матяшеве"]
    z = dict(newly_km2=0, newly_places=[], deocc_km2=0, deocc_places=[], grey_km2=0, grey_places=[])
    res = {"donetsk": {**z, "newly_km2": 5.65}, "luhansk": dict(z)}
    txt = T.build_text("18.11.2026", res, None, None, {"donetsk": n, "luhansk": []})
    assert "Василівка й Матяшеве (" in txt and txt.count("громада)") == 1
    assert "Гришин" not in txt and "—" not in txt


def test_join_and_euphony():
    assert tracker.join_and(["Федорівка", "Миколаївка"]) == "Федорівка й Миколаївка"
    assert tracker.join_and(["Русин Яр", "Бересток"]) == "Русин Яр і Бересток"
    assert tracker.join_and(["А", "Б", "Ізюм"]) == "А, Б і Ізюм"


def test_threads_text_cta_no_source():
    import threads_api
    z = dict(newly_km2=0, newly_places=[], deocc_km2=0, deocc_places=[], grey_km2=0, grey_places=[])
    res = {"donetsk": {**z, "newly_km2": 4.67}, "luhansk": dict(z)}
    t = threads_api.adapt_text(tracker.build_fb_teaser("22.11.2026", res, {"donetsk": ["Федорівка", "Миколаївка"]}))
    assert t.endswith("Підписуйтеся, аби щоранку бачити зміни на мапі Донеччини.")
    assert "DeepStateMap" not in t and "Поширте" not in t and "—" not in t
    q = threads_api.adapt_text(tracker.build_fb_teaser("23.11.2026", {"donetsk": dict(z), "luhansk": dict(z)}))
    assert "Донеччина й Луганщина: за добу на мапі DeepState без змін." in q


def test_yellow_week_text():
    rows = [r for r in tracker.read_history() if "2026-11-17" <= r["date"] <= "2026-11-23"]
    t = tracker.build_yellow_summary("week", rows)
    assert t.startswith("Тиждень на мапі DeepState\n\n")
    assert "– стільки DeepState позначив окупованими на Донеччині." in t
    assert "На Луганщині змін немає." in t and t.endswith(tracker.FB_YELLOW_CTA)


def test_youtube_title_hook_first():
    """24.11.2026: цифра – у перших ~40 знаках назви; опис – щопонеділка + хештеги населених пунктів."""
    import tracker as T
    rows = [r for r in T.read_history() if "2026-11-17" <= r["date"] <= "2026-11-23"]
    if not rows:
        return
    dl = T._period_label(rows[0]["compared_with"], "2026-11-23")
    _, body, places = T.tiktok_caption("week", rows, dl)
    txt = T.youtube_text("week", body, dl, places, rows)
    title = txt.split("Назва:\n")[1].split("\n")[0]
    assert title.startswith("Донеччина: +") and "км²" in title[:40] and title.endswith("| мапа DeepState")
    assert "Щопонеділка" in txt and "#Донеччина #" in txt and "#Shorts" not in txt
