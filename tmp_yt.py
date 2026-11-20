"""Тимчасово (20.11.2026): тижневе порівняння по платформах — у лог."""
import os, re, html, statistics as st
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo
os.environ.setdefault("ANALYTICS_DAYS", "40")
import requests, analytics as A, threads_api, tiktok_api
K = ZoneInfo("Europe/Kyiv")
WEEKS = [date(2026,10,12)+timedelta(days=7*i) for i in range(5)]   # пн: 12.10, 19.10, 26.10, 2.11, 9.11
START = datetime(2026,10,12,tzinfo=K); END = datetime(2026,11,16,tzinfo=K)
def wk(dt):
    d = dt.astimezone(K).date()
    for i, w in enumerate(WEEKS):
        if w <= d < w+timedelta(days=7): return i
    return None
def p(s): return datetime.fromisoformat(s.replace("+0000","+00:00").replace("Z","+00:00"))
def report(name, rows):
    print(f"\n== {name}")
    for i, w in enumerate(WEEKS):
        r = [x for x in rows if x["w"] == i]
        if not r: print(f"W{w:%d.%m} n0"); continue
        reach = [x["reach"] or 0 for x in r]; eng = sum(x["eng"] for x in r); tot = sum(reach)
        ds = [x for x in r if x["t"].startswith("DeepState")]
        top = max(r, key=lambda x: x["reach"] or 0)
        print(f"W{w:%d.%m} n{len(r)} sum{tot} med{st.median(reach):.0f} eng{eng} er{(100*eng/tot if tot else 0):.1f} ds{len(ds)} | top {top['reach']} {top['t'][:50]}")
def fb(pid, name):
    tok = os.getenv("FACEBOOK_PAGE_TOKEN","").strip(); pt = A.get(f"{A.G}/{pid}", fields="access_token", access_token=tok).get("access_token", tok)
    js = A.get(f"{A.G}/{pid}/insights", metric="page_total_media_view_unique", period="day", since=int(START.timestamp()), until=int(END.timestamp()+86400*2), access_token=pt)
    days = {}
    for m in js.get("data", []):
        for v in m.get("values", []):
            d = (p(v["end_time"]) - timedelta(days=1)).date(); days[d] = v["value"]
    print(f"\n== {name} page reach by week:", [sum(days.get(w+timedelta(days=k),0) for k in range(7)) for w in WEEKS])
    rows=[]; url=f"{A.G}/{pid}/posts"; prm=dict(fields="id,message,created_time,shares,reactions.summary(true).limit(0),comments.summary(true).limit(0)", since=int(START.timestamp()), until=int(END.timestamp()), limit=100, access_token=pt)
    while url:
        js = requests.get(url, params=prm, timeout=60).json(); prm=None
        for x in js.get("data", []):
            w = wk(p(x["created_time"]))
            if w is None: continue
            r = None
            for m in ("post_impressions_unique","post_total_media_view_unique"):
                r = A._metric(A.get(f"{A.G}/{x['id']}/insights", metric=m, access_token=pt))
                if r is not None: break
            e = (x.get("reactions") or {}).get("summary",{}).get("total_count",0)+(x.get("comments") or {}).get("summary",{}).get("total_count",0)+(x.get("shares") or {}).get("count",0)
            rows.append(dict(w=w, reach=r, eng=e, t=" ".join((x.get("message") or "").split())))
        url = (js.get("paging") or {}).get("next")
    report(name, rows)
def ig():
    tok = os.getenv("FACEBOOK_PAGE_TOKEN","").strip()
    pg = A.get(f"{A.G}/{A.PAGE_ID}", fields="access_token,instagram_business_account{id}", access_token=tok); pt, igid = pg.get("access_token",tok), pg["instagram_business_account"]["id"]
    days = {}
    for a, b in ((START, START+timedelta(days=29)), (START+timedelta(days=29), END+timedelta(days=1))):
        js = A.get(f"{A.G}/{igid}/insights", metric="reach", period="day", since=int(a.timestamp()), until=int(b.timestamp()), access_token=pt)
        for m in js.get("data", []):
            for v in m.get("values", []): days[(p(v["end_time"])-timedelta(days=1)).date()] = v["value"]
    print("\n== IG account reach by week:", [sum(days.get(w+timedelta(days=k),0) for k in range(7)) for w in WEEKS])
    rows=[]; url=f"{A.G}/{igid}/media"; prm=dict(fields="id,caption,timestamp,like_count,comments_count", limit=50, access_token=pt)
    while url:
        js = requests.get(url, params=prm, timeout=60).json(); prm=None; stop=False
        for m in js.get("data", []):
            t = p(m["timestamp"])
            if t < START: stop=True; break
            w = wk(t)
            if w is None: continue
            v = {x["name"]:(x.get("values") or [{}])[0].get("value") for x in A.get(f"{A.G}/{m['id']}/insights", metric="reach,saved,shares", access_token=pt).get("data",[])}
            rows.append(dict(w=w, reach=v.get("reach"), eng=(m.get("like_count") or 0)+(m.get("comments_count") or 0)+(v.get("saved") or 0)+(v.get("shares") or 0), t=" ".join((m.get("caption") or "").split())))
        url = None if stop else (js.get("paging") or {}).get("next")
    report("Instagram", rows)
def th():
    tok = threads_api._token(); uid = threads_api._user_id()
    print("\n== Threads account views by week:", [threads_api.account_insights(datetime.combine(w,datetime.min.time(),K), datetime.combine(w+timedelta(days=7),datetime.min.time(),K), print).get("views") for w in WEEKS])
    rows=[]; url=f"{threads_api.GRAPH}/{uid}/threads"; prm=dict(fields="id,text,timestamp", since=int(START.timestamp()), until=int(END.timestamp()), limit=100, access_token=tok)
    while url:
        js = requests.get(url, params=prm, timeout=60).json(); prm=None
        for x in js.get("data", []):
            w = wk(p(x["timestamp"]))
            if w is None: continue
            v = {m["name"]:(m.get("values") or [{}])[0].get("value",0) for m in A.get(f"{threads_api.GRAPH}/{x['id']}/insights", metric="views,likes,replies,reposts,quotes", access_token=tok).get("data",[])}
            rows.append(dict(w=w, reach=v.get("views"), eng=sum(v.get(k,0) for k in ("likes","replies","reposts","quotes")), t=" ".join((x.get("text") or "").split())))
        url = (js.get("paging") or {}).get("next")
    report("Threads", rows)
def num(s):
    s=s.strip().replace(",","."); m=1000 if s.endswith("K") else 1
    return int(float(s.rstrip("KM"))*m)
def tg():
    rows=[]; before=None; seen=set()
    for _ in range(60):
        t = requests.get("https://t.me/s/acmc_ua"+(f"?before={before}" if before else ""), timeout=30).text
        bl = t.split('class="tgme_widget_message_wrap')[1:]; ids=[]; old=False
        for b in bl:
            pid = re.search(r'data-post="acmc_ua/(\d+)"', b); dt = re.search(r'datetime="([^"]+)"', b); vw = re.search(r'tgme_widget_message_views">([^<]+)<', b)
            if pid: ids.append(int(pid.group(1)))
            if not dt or not pid or pid.group(1) in seen: continue
            seen.add(pid.group(1)); d = datetime.fromisoformat(dt.group(1))
            if d < START: old=True; continue
            w = wk(d)
            if w is None: continue
            tx = re.search(r'tgme_widget_message_text[^>]*>(.*?)</div>', b, re.S)
            rows.append(dict(w=w, reach=num(vw.group(1)) if vw else 0, eng=0, t=html.unescape(re.sub("<[^>]+>"," ",tx.group(1))).strip() if tx else ""))
        if old or not ids: break
        before = min(ids)
    report("Telegram", rows)
def tt():
    tok = tiktok_api.access_token(print); h={"Authorization":f"Bearer {tok}","Content-Type":"application/json"}; rows=[]; cur=None
    for _ in range(5):
        js = requests.post(f"{tiktok_api.API}/video/list/", headers=h, json={"max_count":20, **({"cursor":cur} if cur else {})}, params={"fields":"id,title,create_time,view_count,like_count,comment_count,share_count"}, timeout=30).json()
        d = js.get("data") or {}
        for v in d.get("videos", []):
            w = wk(datetime.fromtimestamp(v["create_time"], timezone.utc))
            if w is not None: rows.append(dict(w=w, reach=v.get("view_count"), eng=(v.get("like_count") or 0)+(v.get("comment_count") or 0)+(v.get("share_count") or 0), t=v.get("title") or ""))
        if not d.get("has_more"): break
        cur = d.get("cursor")
    report("TikTok", rows)
for f in (lambda: fb(A.YELLOW_PAGE_ID,"FB жовта"), lambda: fb(A.PAGE_ID,"FB біла"), ig, th, tg, tt):
    try: f()
    except Exception as e: print("ERR", type(e).__name__, e)
