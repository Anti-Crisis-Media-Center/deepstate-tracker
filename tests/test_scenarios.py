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
    assert change_map.step_groups(f) == [2, 3]


def test_one_direction_no_steps(tmp_path):
    f = [write_day(tmp_path, "2026-01-01", [(A, "occupied")]),
         write_day(tmp_path, "2026-01-02", [(B, "occupied")])]
    assert change_map.step_groups(f) == []


REAL_WEEK = [f"2026-11-0{i}" for i in range(3, 10)]


@pytest.mark.skipif(not (tracker.CHANGES_DIR / "2026-11-05.geojson").exists(), reason="немає даних тижня 2–9.11")
def test_names_real_week():
    """Справжній тиждень 3–9.11.2026: сусідні ділянки (<3 км, з 12.11) — одна назва (Добропілля + Білицьке
    за 1,8 км → «Білицьке»); Покровськ за 4,1 км — окрема назва; місто лише ближче 10 км; до 4 назв."""
    assert tracker.place_names(REAL_WEEK) == ["Покровськ", "Білицьке", "Русин Яр", "Костянтинівка"]


def test_names_small_separate_area():
    """12.11.2026: ділянка 0,37 км² за 4,4 км від Костянтинівки — окрема назва (Часів Яр)."""
    assert tracker.place_names(["2026-11-12"]) == ["Костянтинівка", "Часів Яр"]


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
    """12.11.2026: пункти з повідомлень DeepState — окремим рядком лише в Telegram; у головні назви
    (графіка) їх не додаємо. Відмінки розпізнаються («у Костянтинівці», «Русиного Яру»)."""
    pn = list(tracker.place_names(["2026-11-05"]))
    assert tracker.deepstate_mentioned(["2026-11-05"])[:2] == ["Костянтинівка", "Русин Яр"]
    assert tracker.deepstate_mentioned(["2026-11-05"], skip=pn) == []   # без повторів головних назв
    r = {k: {"newly_km2": 1.0, "deocc_km2": 0, "grey_km2": 0, "newly_places": [], "deocc_places": [],
             "grey_places": []} for k in tracker.OBLASTS}
    txt = tracker.build_text("05.11", r, mentioned={"donetsk": ["Русин Яр"]}, names={"donetsk": pn})
    assert "Також у повідомленнях DeepState: Русин Яр." in txt and "(" not in txt.split("Повідомлення")[0]
