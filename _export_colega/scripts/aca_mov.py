import sys, re
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
ROOT=Path(__file__).resolve().parent
OUT=ROOT/"scraper"/"out"/"aca_nav"; OUT.mkdir(parents=True,exist_ok=True)
n=[0]
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9335")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    def on_resp(r):
        try:
            u=r.url
            if any(s in u for s in [".js",".css",".png",".svg",".ico",".woff"]): return
            body=r.text()
            if re.search(r"\b\d{11}\b|humed|analis|merma|hectol|dañad|quebr|romaneo|carta",body,re.I):
                n[0]+=1; safe=re.sub(r"[^a-z0-9]+","_",re.sub(r"https?://","",u).split("?")[0].lower())[:42]
                (OUT/f"mov{n[0]:02d}_{safe}.txt").write_text(body[:200000],encoding="utf-8")
        except: pass
    page.on("response",on_resp)
    fr=next((f for f in page.frames if "Totales2" in f.url), None)
    if fr:
        # click Saldo Actual (Mov_ctacte)
        try: fr.locator("a:has-text('Saldo Actual')").first.click(timeout=5000)
        except Exception as e: print("click:",str(e)[:50])
    page.wait_for_timeout(4000)
    # buscar en todos los frames una tabla con CTG (11 dig)
    for f in page.frames:
        try:
            h=f.evaluate("""()=>{const ths=Array.from(document.querySelectorAll('th,td')).map(c=>c.innerText.trim());
                const ctg=ths.find(t=>/^\d{11}$/.test(t)); 
                const heads=Array.from(document.querySelectorAll('th')).map(t=>t.innerText.trim()).filter(Boolean).slice(0,15);
                return {hasCTG:!!ctg, sampleCTG:ctg||'', heads:heads};}""")
        except: continue
        if h["hasCTG"] or h["heads"]:
            print("FRAME:",f.url[-45:],"| CTG?",h["hasCTG"],h["sampleCTG"],"| heads:",h["heads"])
    print("capturas:",n[0])
    b.close()
