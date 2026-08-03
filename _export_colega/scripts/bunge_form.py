import sys
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9333")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    print("URL:", page.url)
    info=page.evaluate("""()=>{
        const sel=Array.from(document.querySelectorAll('select')).map(s=>({id:s.id,name:s.name,
            opts:Array.from(s.options).slice(0,15).map(o=>o.text.trim())}));
        const inp=Array.from(document.querySelectorAll('input')).filter(e=>e.type!=='hidden').map(e=>({id:e.id,type:e.type,ph:e.placeholder,val:e.value}));
        const btn=Array.from(document.querySelectorAll('a,button,input[type=submit],input[type=button]')).map(e=>(e.innerText||e.value||'').trim()).filter(Boolean).slice(0,30);
        return {sel,inp,btn};
    }""")
    import json; print(json.dumps(info,ensure_ascii=False,indent=1))
    b.close()
