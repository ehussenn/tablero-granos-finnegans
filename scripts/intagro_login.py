import sys, json
from playwright.sync_api import sync_playwright
from _env import need
sys.stdout.reconfigure(encoding="utf-8")
email = sys.argv[1] if len(sys.argv)>1 else need("FINNEGANS_WEB_USER")
pwd   = sys.argv[2] if len(sys.argv)>2 else need("INTAGRO_PASS")
resp=[]
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9334")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    def on_resp(r):
        try:
            if r.request.method=="POST" and any(k in r.url.lower() for k in ["login","auth","token","sesion","ingres","sign"]):
                body=""
                try: body=r.text()[:200]
                except: pass
                resp.append((r.status, r.url[:80], body))
        except: pass
    page.on("response", on_resp)
    print("antes:", page.url)
    page.fill("#email", email); page.fill("#password", pwd)
    page.wait_for_timeout(300)
    clicked=False
    for sel in ["button:has-text('Ingresar')","a:has-text('Ingresar')","input[type=submit]","text=Ingresar"]:
        try:
            if page.locator(sel).count()>0:
                page.locator(sel).first.click(timeout=4000); clicked=True
                print("click via", sel); break
        except Exception as e: print("x",sel,str(e)[:40])
    if not clicked:
        page.keyboard.press("Enter"); print("enter")
    page.wait_for_timeout(5500)
    print("despues:", page.url)
    for s,u,bd in resp: print("  LOGIN RESP:", s, u, "|", bd)
    b.close()
