import os, requests
tok=os.getenv("ANALYTICS_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN"); chat=os.getenv("ANALYTICS_CHAT_ID") or os.getenv("TELEGRAM_ALERT_CHAT_ID")
print("bot:", requests.get(f"https://api.telegram.org/bot{tok}/getMe").json().get("result",{}).get("username"))
print("chat:", requests.get(f"https://api.telegram.org/bot{tok}/getChat",params={"chat_id":chat}).json())
