import os, requests
for name in ("TELEGRAM_BOT_TOKEN","ANALYTICS_BOT_TOKEN"):
    tok=os.getenv(name); chat=os.getenv("TELEGRAM_ALERT_CHAT_ID")
    r=requests.get(f"https://api.telegram.org/bot{tok}/getChat",params={"chat_id":chat}).json()
    mig=(r.get("parameters") or {}).get("migrate_to_chat_id")
    print(name, "ok" if r.get("ok") else r.get("description"), "NEW_ID:", mig if mig else (r.get("result") or {}).get("id"))
