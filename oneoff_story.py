"""Разово: сторіз за 08.11 (вранці не вийшли через помилку)."""
from datetime import datetime
import tracker as t
r = {k: {"newly_km2": 0, "deocc_km2": 0, "newly_places": [], "deocc_places": []} for k in t.OBLASTS}
t.make_and_post_story(t.COVER_IMAGE, t._date_label(datetime(2026, 11, 8).date()), t.story_blocks_daily(r))
