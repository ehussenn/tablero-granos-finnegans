import sys, re
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
ROOT=Path(__file__).resolve().parent
OUT=ROOT/"scraper"/"out"/"aca_nav"; OUT.mkdir(parents=True,exist_ok=True)
target=sys.argv[1] if len(sys.argv)>1 else "Resúmenes de Cuenta"
n=[0]
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9335")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    def on_resp(r):
        try:
            u=r.url; 
            if any(s in u for s in [".js",".css",".woff",".png",".svg",".ico",".jpg"]): return
            body=r.text()
            if re.search(r"humed|calid|analis|merma|hectol|dañad|danad|quebr|CTG|carta",body,re.I) and len(body)<300000:
                n[0]+=1; safe=re.sub(r"[^a-z0-9]+","_",re.sub(r"https?://","",u).split("?")[0].lower())[:45]
                (OUT/f"{n[0]:02d}_{safe}.txt").write_text(body[:200000],encoding="utf-8")
        except: pass
    page.on("response",on_resp)
    try:
        page.get_by_role("link",name=re.compile(target,re.I)).first.click(timeout=6000)
    except Exception as e:
        try: page.locator(f"text={target}").first.click(timeout=4000)
        except Exception as e2: print("click err:",str(e2)[:50])
    page.wait_for_timeout(4000)
    print("URL:",page.url)
    # submenu / opciones nuevas
    links=page.evaluate("""()=>Array.from(document.querySelectorAll('a')).map(a=>({t:(a.innerText||'').trim(),h:(a.getAttribute('href')||'').slice(0,40)})).filter(x=>x.t&&x.t.length<40).slice(0,40)""")
    for l in links[:35]: print("  ",l["t"],"|",l["h"])
    print("capturas calidad:",n[0])
    b.close()
