import os, requests, threads_api as t
from datetime import datetime, timedelta, timezone
G="https://graph.facebook.com/v24.0"; P="111460408546782"
tok=os.environ["FACEBOOK_PAGE_TOKEN"]
pg=requests.get(f"{G}/{P}",params={"fields":"access_token,instagram_business_account","access_token":tok}).json()
pt=pg["access_token"]; ig=pg["instagram_business_account"]["id"]
now=datetime.now(timezone.utc)
for w in range(4):
    a=now-timedelta(days=7*(w+1)); b=now-timedelta(days=7*w)
    r=requests.get(f"{G}/{ig}/insights",params={"metric":"reach","period":"day","since":int(a.timestamp()),"until":int(b.timestamp()),"access_token":pt}).json()
    vals=[v["value"] for m in r.get("data",[]) for v in m.get("values",[])]
    f=requests.get(f"{G}/{P}/insights",params={"metric":"page_impressions_unique","period":"day","since":int(a.timestamp()),"until":int(b.timestamp()),"access_token":pt}).json()
    fv=[v["value"] for m in f.get("data",[]) for v in m.get("values",[])]
    th=t.account_insights(a,b,print)
    print(f"тиждень -{w}: IG {sum(vals)} {vals} | FB {sum(fv)} {fv} | Threads {th.get('views')}", r.get("error",{}).get("message",""))
