"""Разова перевірка маркера Threads без публікацій."""
import requests, threads_api as t
tok = t._token()
if not tok:
    print("THREADS_TOKEN порожній"); raise SystemExit(1)
me = requests.get(f"{t.GRAPH}/me", params={"fields": "id,username,name", "access_token": tok}, timeout=30).json()
print("Акаунт:", {k: me.get(k) for k in ("username", "name")} if "error" not in me else me["error"].get("message"))
print("Підписники:", t.followers(print))
from datetime import datetime, timedelta
now = datetime.now()
print("Статистика за 7 днів:", t.account_insights(now - timedelta(days=7), now, print))

import os
gh = os.getenv("GH_ADMIN_TOKEN", "").strip()
if gh:
    r = requests.get(f"https://api.github.com/repos/{t.REPO}/actions/secrets",
                     headers={"Authorization": f"Bearer {gh}", "Accept": "application/vnd.github+json"}, timeout=30)
    names = [s["name"] for s in r.json().get("secrets", [])] if r.ok else r.text[:200]
    print("GH_ADMIN_TOKEN:", "працює" if r.ok else f"помилка {r.status_code}", names)
else:
    print("GH_ADMIN_TOKEN порожній")
