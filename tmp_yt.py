"""Тимчасово (20.11.2026): плейлист «Перевірено на Сході» на основному каналі — у лог."""
import os, requests
API = "https://www.googleapis.com/youtube/v3"; KEY = os.environ["YOUTUBE_API_KEY"]
def g(p, **kw): kw["key"] = KEY; return requests.get(f"{API}/{p}", params=kw, timeout=40).json()
for ref in [r.strip() for r in os.environ.get("YOUTUBE_CHANNELS", "").split(",") if r.strip()]:
    q = {"id": ref} if ref.startswith("UC") else {"forHandle": ref if ref.startswith("@") else "@" + ref}
    ch = g("channels", part="snippet", **q)["items"][0]
    pls, tok = [], None
    while True:
        js = g("playlists", part="snippet,contentDetails", channelId=ch["id"], maxResults=50, **({"pageToken": tok} if tok else {}))
        pls += js.get("items", []); tok = js.get("nextPageToken")
        if not tok: break
    print("\n== CHANNEL", ch["snippet"]["title"], "| playlists:", "; ".join(f"{p['snippet']['title']} ({p['contentDetails']['itemCount']})" for p in pls))
    for p in pls:
        if "перевірено" not in p["snippet"]["title"].lower(): continue
        ids, tok = [], None
        while True:
            js = g("playlistItems", part="contentDetails", playlistId=p["id"], maxResults=50, **({"pageToken": tok} if tok else {}))
            ids += [x["contentDetails"]["videoId"] for x in js.get("items", [])]; tok = js.get("nextPageToken")
            if not tok: break
        print(f"\n== PLAYLIST {p['snippet']['title']} ({len(ids)})")
        for i in range(0, len(ids), 50):
            for v in g("videos", part="snippet,statistics,contentDetails", id=",".join(ids[i:i+50])).get("items", []):
                s = v["statistics"]
                print(f"V {v['snippet']['publishedAt'][:10]} | {v['contentDetails']['duration']} | views {s.get('viewCount')} | likes {s.get('likeCount')} | comm {s.get('commentCount')} | {v['snippet']['title'][:100]}")
