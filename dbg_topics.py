import os, requests
chat=os.getenv("TELEGRAM_ALERT_CHAT_ID")
for name,thr,txt in (("TELEGRAM_BOT_TOKEN","6","🧪 Перевірка: DeepState-бот бачить гілку «Тести»."),
                     ("ANALYTICS_BOT_TOKEN","6","🧪 Перевірка: Аналітика-бот бачить гілку «Тести».")):
    tok=os.getenv(name)
    r=requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",data={"chat_id":chat,"message_thread_id":thr,"text":txt}).json()
    print(name, "OK" if r.get("ok") else r.get("description"))
