"""Sniffer: encuentra de dónde sale la CALIDAD en el extranet de Cargill.
Abre Movements + Documents, clickea filas, y captura todas las respuestas de
api.cglcloud.com buscando campos de calidad con valores != 0."""
import sys, json, re, time
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "scraper" / ".cargill_profile"
OUT = ROOT / "scraper" / "out"; OUT.mkdir(parents=True, exist_ok=True)
QUAL = ["humed","impure","ardid","avari","partid","verde","calid","quality","grade","analis","disc"]
HITS = []

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(user_data_dir=str(PROFILE), headless=True,
        viewport={"width":1500,"height":950}, args=["--disable-blink-features=AutomationControlled"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    def on_resp(r):
        try:
            u = r.url
            if "api.cglcloud.com" not in u: return
            ct = r.headers.get("content-type","")
            if "json" not in ct: return
            body = r.text()
            low = body.lower()
            score = sum(low.count(q) for q in QUAL)
            HITS.append((u.split("?")[0], r.request.url, score, len(body), body[:0]))
            if score>0:
                # guardar respuestas con señales de calidad
                fn = OUT / ("cgq_"+re.sub(r'[^a-z0-9]+','_',u.split('cglcloud.com')[-1].split('?')[0].lower())[:40]+f"_{len(HITS)}.json")
                fn.write_text(body, encoding="utf-8")
        except Exception: pass
    page.on("response", on_resp)
    print("[+] Movements..."); page.goto("https://www.mycargill.com/cascsa/v2/app/Movements", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(9000)
    if "/login" in page.url: print("[X] sesion vencida"); ctx.close(); sys.exit(1)
    # clickear la primera fila de la grilla para abrir detalle
    for sel in ["table tbody tr","[role=row]","div[class*=row]","tr"]:
        try:
            rows = page.locator(sel)
            if rows.count()>1:
                rows.nth(1).click(timeout=4000); print(f"[+] click fila via {sel}"); break
        except Exception: pass
    page.wait_for_timeout(5000)
    # tambien Documents
    print("[+] Documents...");
    try:
        page.goto("https://www.mycargill.com/cascsa/v2/app/Documents", wait_until="domcontentloaded", timeout=60000); page.wait_for_timeout(7000)
    except Exception: pass
    ctx.close()

print("\n=== ENDPOINTS api.cglcloud.com con señales de calidad (score>0) ===")
seen=set()
for base, full, score, ln, _ in sorted(HITS, key=lambda x:-x[2]):
    if score>0 and base not in seen:
        seen.add(base); print(f"  score {score:>3}  {base}")
print(f"\n[+] total responses capturadas: {len(HITS)} · con calidad: {sum(1 for h in HITS if h[2]>0)}")
print("[+] respuestas con calidad guardadas en scripts/scraper/out/cgq_*.json")
