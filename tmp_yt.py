"""Тимчасово (20.11.2026): усі відео «Перевірено на Сході» зі статистикою — у лог."""
import os, re, requests
API = "https://www.googleapis.com/youtube/v3"; KEY = os.environ["YOUTUBE_API_KEY"]
def g(p, **kw): kw["key"] = KEY; return requests.get(f"{API}/{p}", params=kw, timeout=40).json()
for ref in [r.strip() for r in os.environ.get("YOUTUBE_CHANNELS", "").split(",") if r.strip()]:
    q = {"id": ref} if ref.startswith("UC") else {"forHandle": ref if ref.startswith("@") else "@" + ref}
    ch = g("channels", part="snippet,statistics,contentDetails", **q)["items"][0]
    print("\n== CHANNEL", ch["snippet"]["title"], ch["statistics"])
    up = ch["contentDetails"]["relatedPlaylists"]["uploads"]; ids = []; tok = None
    while True:
        js = g("playlistItems", part="contentDetails", playlistId=up, maxResults=50, **({"pageToken": tok} if tok else {}))
        ids += [x["contentDetails"]["videoId"] for x in js.get("items", [])]
        tok = js.get("nextPageToken")
        if not tok: break
    for i in range(0, len(ids), 50):
        for v in g("videos", part="snippet,statistics,contentDetails", id=",".join(ids[i:i+50])).get("items", []):
            s = v["statistics"]; d = v["contentDetails"]["duration"]
            print(f"{v['snippet']['publishedAt'][:10]} | {d} | views {s.get('viewCount')} | likes {s.get('likeCount')} | comm {s.get('commentCount')} | {v['snippet']['title'][:90]}")
