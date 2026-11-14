"""Разова перевірка: статистика живих сторіз Instagram і Facebook (біла). Нічого не публікує."""
import os, requests
G = "https://graph.facebook.com/v24.0"
tok = os.getenv("FACEBOOK_PAGE_TOKEN", "").strip()
PAGE = os.getenv("FACEBOOK_PAGE_ID", "111460408546782")
pg = requests.get(f"{G}/{PAGE}", params={"fields": "access_token,instagram_business_account", "access_token": tok}, timeout=30).json()
ptok = pg.get("access_token", tok); ig = (pg.get("instagram_business_account") or {}).get("id")
print("IG:", ig)
st = requests.get(f"{G}/{ig}/stories", params={"fields": "id,timestamp,media_type,permalink", "access_token": ptok}, timeout=30).json()
for s in st.get("data", []):
    out = {}
    for m in ("reach", "views", "replies", "navigation", "shares", "total_interactions", "follows", "profile_visits"):
        r = requests.get(f"{G}/{s['id']}/insights", params={"metric": m, "access_token": ptok}, timeout=30).json()
        d = r.get("data")
        if d:
            v = d[0].get("values", [{}])[0].get("value") if d[0].get("values") else d[0].get("total_value", {}).get("value")
            out[m] = v
        else:
            out[m] = "err:" + str(r.get("error", {}).get("message", ""))[:60]
    print("IG story", s.get("timestamp"), s.get("media_type"), out)
if not st.get("data"):
    print("IG stories:", st)
fs = requests.get(f"{G}/{PAGE}/stories", params={"access_token": ptok}, timeout=30).json()
print("FB stories:", str(fs)[:800])
for s in (fs.get("data") or [])[:5]:
    mid = s.get("post_id") or s.get("media_id")
    for m in ("story_impressions", "page_story_impressions_by_story_id", "post_impressions_unique"):
        r = requests.get(f"{G}/{mid}/insights", params={"metric": m, "access_token": ptok}, timeout=30).json()
        print("  FB", mid, m, str(r)[:200])
