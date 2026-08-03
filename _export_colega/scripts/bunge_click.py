import sys
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
target = sys.argv[1] if len(sys.argv)>1 else "Descarga de Análisis"
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9333")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    r=page.evaluate("""(t)=>{
        const as=Array.from(document.querySelectorAll('a'));
        const a=as.find(x=>(x.innerText||'').trim().toLowerCase()===t.toLowerCase())
              || as.find(x=>(x.innerText||'').trim().toLowerCase().includes(t.toLowerCase()));
        if(!a) return 'NO ENCONTRADO';
        a.click(); return 'click: '+(a.innerText||'').trim().slice(0,40);
    }""", target)
    print("[+]", r)
    page.wait_for_timeout(4500)
    print("URL:", page.url)
    b.close()
