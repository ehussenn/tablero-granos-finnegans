import sys
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
USER=sys.argv[1] if len(sys.argv)>1 else "agronasaja"
PWD =sys.argv[2] if len(sys.argv)>2 else "<PASSWORD>"
reqs=[]
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9335")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    def on_resp(r):
        try:
            u=r.url
            if any(s in u for s in [".js",".css",".woff",".png",".svg",".ico",".jpg",".gif"]): return
            if r.request.method=="POST" or "asp" in u.lower():
                bd=""; 
                try: bd=r.text()[:160]
                except: pass
                reqs.append((r.request.method,r.status,u[:80],r.request.post_data,bd))
        except: pass
    page.on("response",on_resp)
    page.goto("https://www.acabase.com.ar/",wait_until="domcontentloaded"); page.wait_for_timeout(2500)
    page.evaluate("""(c)=>{const u=document.getElementById('usuario'),p=document.getElementById('password');
        const set=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
        set.call(u,c.u); set.call(p,c.p);
        u.dispatchEvent(new Event('input',{bubbles:true})); p.dispatchEvent(new Event('input',{bubbles:true}));
        const ing=document.getElementById('ingresar'); if(ing)ing.click();}""",{"u":USER,"p":PWD})
    page.wait_for_timeout(6000)
    print("despues:",page.url)
    for m,s,u,pd,bd in reqs[-10:]:
        print(f"  {m} {s} {u} | post={str(pd)[:60]} | resp={bd[:60]}")
    b.close()
