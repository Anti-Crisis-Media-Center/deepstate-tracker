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
    """Справжній тиждень 2–9.11.2026: сусідні ділянки (<5 км) — одна назва; місто лише ближче 10 км; до 4 назв."""
    assert tracker.place_names(REAL_WEEK) == ["Білицьке", "Русин Яр", "Костянтинівка"]


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
        == "поблизу Добропілля та Білицького (Василівка, Світле)"
    assert tracker.near_phrase(["Русин Яр"], ["Русин Яр (Краматорський р-н)"]) == "поблизу: Русин Яр"
