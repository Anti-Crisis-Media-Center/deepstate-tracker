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
