"""Разова перевірка маркера Meta (Facebook + Instagram) без публікацій."""
import os, requests
G = "https://graph.facebook.com/v24.0"
PAGE = os.getenv("FACEBOOK_PAGE_ID", "111460408546782")
tok = os.getenv("FACEBOOK_PAGE_TOKEN", "").strip()
perm = requests.get(f"{G}/me/permissions", params={"access_token": tok}, timeout=30).json()
granted = sorted(p["permission"] for p in perm.get("data", []) if p.get("status") == "granted")
print("Дозволи:", granted or perm)
need = ["pages_manage_posts", "read_insights", "instagram_basic", "instagram_content_publish", "instagram_manage_insights"]
print("Бракує:", [p for p in need if p not in granted] or "нічого")
pg = requests.get(f"{G}/{PAGE}", params={"fields": "access_token,name,followers_count,instagram_business_account{id,username,followers_count}", "access_token": tok}, timeout=30).json()
if "error" in pg:
    print("Сторінка: помилка", pg["error"].get("message")); raise SystemExit
print("Сторінка:", pg.get("name"), "| підписники:", pg.get("followers_count"))
ptok = pg.get("access_token", tok)
ig = pg.get("instagram_business_account")
print("Instagram:", ig or "не видно")
ins = requests.get(f"{G}/{PAGE}/insights", params={"metric": "page_impressions_unique,page_post_engagements", "period": "week", "access_token": ptok}, timeout=30).json()
print("Статистика FB:", [(m["name"], m["values"][-1]["value"]) for m in ins.get("data", [])] or ins.get("error", {}).get("message"))
if ig:
    ii = requests.get(f"{G}/{ig['id']}/insights", params={"metric": "reach", "period": "day", "access_token": ptok}, timeout=30).json()
    print("Статистика IG:", [(m["name"], m["values"][-1]["value"]) for m in ii.get("data", [])] or ii.get("error", {}).get("message"))
