import sys
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
email = sys.argv[1] if len(sys.argv)>1 else "<USER_EMAIL>"
pwd   = sys.argv[2] if len(sys.argv)>2 else "<PASSWORD>"
reqs=[]
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9334")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    def on_resp(r):
        try:
            u=r.url
            if any(s in u for s in [".js",".css",".woff",".png",".svg",".gif",".ico",".map"]): return
            if r.request.method in ("POST","GET") and ("api" in u or r.request.method=="POST"):
                bd=""
                try: bd=r.text()[:150]
                except: pass
                reqs.append((r.request.method, r.status, u[:75], bd))
        except: pass
    page.on("response", on_resp)
    if not page.evaluate("()=>!!document.getElementById('email')"):
        page.goto("https://portal.intagro.com/", wait_until="domcontentloaded"); page.wait_for_timeout(2500)
    page.fill("#email", email); page.fill("#password", pwd); page.wait_for_timeout(300)
    page.locator("button:has-text('Ingresar')").first.click(timeout=4000)
    page.wait_for_timeout(6000)
    print("despues:", page.url)
    for m,s,u,bd in reqs: print(f"  {m} {s} {u} | {bd}")
    err=page.evaluate("""()=>{const all=document.body.innerText||'';
        const m=all.match(/(incorrect|inv[aá]lid|error|no .{0,20}(existe|coincide|encontr)|credencial|contrase)/i);
        return m?all.slice(Math.max(0,all.indexOf(m[0])-20), all.indexOf(m[0])+80):'';}""")
    print("err-text:", err if err else "(nada)")
    b.close()
