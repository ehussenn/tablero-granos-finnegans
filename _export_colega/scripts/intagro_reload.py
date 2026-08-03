import sys
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9334")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    print("estado:", page.url)
    page.goto("https://portal.intagro.com/", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    print("recargado:", page.url)
    has=page.evaluate("()=>!!document.getElementById('email')")
    print("tiene form login:", has)
    b.close()
