import sys
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9333")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    # clickear el link de la cuenta 123879 (Granos) dentro del contenido
    r=page.evaluate("""()=>{
        const as=Array.from(document.querySelectorAll('a'));
        // preferir el del content placeholder
        let a=as.find(x=>/123879/.test(x.innerText)&&/cph_conte|ucInfoAct/.test(x.getAttribute('href')||''));
        if(!a) a=as.find(x=>/123879/.test(x.innerText)&&(x.getAttribute('href')||'').includes('doPostBack'));
        if(!a) return 'no link';
        a.click(); return 'click '+(a.getAttribute('href')||'').slice(0,60);
    }""")
    print("[+]", r)
    page.wait_for_timeout(4000)
    print("URL:", page.url)
    b.close()
