#!/usr/bin/env python3
"""
One-off diagnostic: check whether BrickLink's classic price-guide page is
reachable and scrapable from a network-enabled runner (this sandbox can't
reach bricklink.com directly). Saves each fetched page to /tmp/pg_<code>.html
for inspection and prints a short JSON summary per code.

    python carousel/fetch_price_probe.py sh0065 sh0066 sh0067
"""
import sys
import json
import urllib.request


def fetch(code: str) -> dict:
    url = f"https://www.bricklink.com/catalogPG.asp?M={code}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    })
    try:
        html = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")
    except Exception as e:
        return {"code": code, "error": str(e)}
    return {"code": code, "html_len": len(html), "has_used_table": "Used" in html, "html": html}


if __name__ == "__main__":
    for c in sys.argv[1:]:
        r = fetch(c)
        html = r.pop("html", "")
        print(json.dumps(r))
        if html:
            with open(f"/tmp/pg_{r['code']}.html", "w", encoding="utf-8") as f:
                f.write(html)
