import sys
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9340")
    ctx=b.contexts[0]
    for pg in ctx.pages: print("TAB:", pg.url[:140])
    b.close()
