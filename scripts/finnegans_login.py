import os, sys
sys.path.insert(0,'scripts'); sys.path.insert(0,'.')
import finnegans_api  # carga .env
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
U=os.environ.get("FINNEGANS_WEB_USER"); P=os.environ.get("FINNEGANS_WEB_PASSWORD"); W=os.environ.get("FINNEGANS_WORKSPACE")
print("user:",(U or "")[:6]+"...","| workspace:",W)
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9340")
    ctx=b.contexts[0]; page=next((pg for pg in ctx.pages if "login" in pg.url), ctx.pages[-1])
    if "login" not in page.url:
        page.goto("https://services.finneg.com/login",wait_until="domcontentloaded"); page.wait_for_timeout(3000)
    r=page.evaluate("""(c)=>{
        const set=(id,v)=>{const e=document.getElementById(id); if(e){const s=Object.getOwnPropertyDescriptor(e.__proto__,'value').set; s.call(e,v); e.dispatchEvent(new Event('input',{bubbles:true})); e.dispatchEvent(new Event('change',{bubbles:true})); return true;} return false;};
        const a=set('loginname',c.u), b=set('loginpassword',c.p), d=set('logincompany',c.w);
        return {loginname:a,pass:b,company:d};
    }""",{"u":U,"p":P,"w":W})
    print("campos:",r)
    page.wait_for_timeout(500)
    # submit
    clicked=False
    for sel in ["button[name=standardSubmit]","#standardSubmit","button:has-text('Ingresar')","button:has-text('Iniciar')","button[type=submit]"]:
        try:
            if page.locator(sel).count()>0: page.locator(sel).first.click(timeout=4000); clicked=True; print("submit via",sel); break
        except Exception as e: pass
    if not clicked: page.keyboard.press("Enter"); print("enter")
    page.wait_for_timeout(8000)
    print("URL despues:",page.url)
    print("body:", page.evaluate("()=>document.body.innerText.slice(0,200)").replace(chr(10)," ")[:200])
    b.close()
