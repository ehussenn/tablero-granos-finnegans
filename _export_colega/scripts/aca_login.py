import sys
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
USER=sys.argv[1] if len(sys.argv)>1 else "agronasaja"
PWD =sys.argv[2] if len(sys.argv)>2 else "<PASSWORD>"
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9335")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    print("antes:",page.url)
    # abrir el panel de identificación si hace falta
    page.evaluate("""()=>{const id=document.getElementById('identificarse')||Array.from(document.querySelectorAll('a,button')).find(e=>/identificarse/i.test((e.innerText||'').trim())); if(id)id.click();}""")
    page.wait_for_timeout(1200)
    r=page.evaluate("""(c)=>{
        const u=document.getElementById('usuario'), p=document.getElementById('password');
        if(!u||!p) return 'no fields';
        const set=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
        set.call(u,c.u); set.call(p,c.p);
        u.dispatchEvent(new Event('input',{bubbles:true})); p.dispatchEvent(new Event('input',{bubbles:true}));
        const ing=document.getElementById('ingresar'); if(ing){ing.click(); return 'ingresar-click';}
        return 'no ingresar';
    }""",{"u":USER,"p":PWD})
    print("login:",r)
    page.wait_for_timeout(6500)
    print("despues:",page.url)
    body=page.evaluate("()=>document.body.innerText.slice(0,400)")
    print("body:",body.replace(chr(10)," ")[:300])
    b.close()
