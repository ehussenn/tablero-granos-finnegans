"""Prueba endpoints LDC con un CTG específico (10130972914 del DW)."""
import sys, os, json
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / "scripts" / "scraper" / ".ldc_profile"
OUT = ROOT / "scripts" / "scraper" / "out" / "ldc"
OUT.mkdir(parents=True, exist_ok=True)

env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

USER = os.environ.get("LDC_USER", "pgauto@agronasaja.com.ar")
PWD = os.environ.get("LDC_PASS", "Nasaja1234.")
CTG = "10130972914"  # sample del DW
API = "https://mildc.com/Dreyfus.Extranet.Site.UI.Services/api"
TOKEN = [None]

def on_req(r):
    if "mildc.com" in r.url:
        a = r.headers.get("authorization", "")
        if a and "bearer" in a.lower() and not TOKEN[0]: TOKEN[0] = a

from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(user_data_dir=str(PROFILE), headless=False, viewport={"width":1500,"height":950})
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.on("request", on_req)
    page.goto("https://mildc.com/webportal/dashboard", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(8000)
    if page.locator("input[type='password']").count() > 0:
        page.locator("input[type='text']").first.fill(USER)
        page.locator("input[type='password']").first.fill(PWD)
        page.locator("button[type='submit']").first.click()
        page.wait_for_timeout(12000)
        page.goto("https://mildc.com/webportal/dashboard", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(8000)
    if not TOKEN[0]: print("[!] no token"); ctx.close(); sys.exit(1)
    print(f"[+] Token OK")

    H = {"authorization": TOKEN[0], "accept": "application/json", "content-type": "application/json"}

    # Lista de variantes para probar con CTG
    candidatos = [
        ("/ApplicationsQuality/ReadByCriteria", {"CarriageDocumentNumber": CTG, "ConditionalKey":"GroupB"}),
        ("/MovementsQuantity/ReadByCriteria", {"CarriageDocumentNumber": CTG, "ConditionalKey":"GroupB"}),
        ("/MovementsQuantity/ReadByCriteria", {"CTG": CTG, "ConditionalKey":"GroupB"}),
        ("/MovementsQuantity/ReadByCriteria", {"CarriageDocumentNumber": CTG}),
        ("/ApplicationsQuality/ReadByCriteria", {"CarriageDocumentNumber": CTG}),
        ("/Deliveries/ReadByCriteria", {"CTG": CTG}),
        ("/Receptions/ReadByCriteria", {"CTG": CTG}),
        # Probar también sin CTG, payload minimal
        ("/MovementsQuantity/ReadByCriteria", {"ConditionalKey":"GroupB","DateFrom":"2026-01-01T00:00:00Z","DateUntil":"2026-12-31T00:00:00Z","CompanyDocumentNumber":"30710712758"}),
    ]
    for ep, body in candidatos:
        try:
            r = page.context.request.post(f"{API}{ep}", headers=H, data=json.dumps(body), timeout=30000)
            t = r.text()
            n = 0
            try:
                j = json.loads(t)
                if isinstance(j, list): n = len(j)
                elif isinstance(j, dict) and "List" in j: n = len(j["List"]) if isinstance(j["List"], list) else 0
            except: pass
            print(f"  [{r.status:3d}] {ep[:40]:40s} body={json.dumps(body)[:60]:60s} -> {n} items / {t[:120]}")
            if r.status == 200 and n > 0:
                safe_ep = ep.replace("/","_").strip("_")
                (OUT/f"probe_{safe_ep}.json").write_text(t, encoding="utf-8")
        except Exception as e:
            print(f"  ERR {ep}: {str(e)[:80]}")

    # Listar todos los CTGs que tenemos en el dataset Settlements
    print(f"\n[+] Inspecting Settlement detalle (settle ID 3071763)...")
    detalle_endpoints = [
        ("/Settlements/ReadDetail", {"settlementID": 3071763}),
        ("/Settlements/ReadDetailByID", {"settlementID": 3071763}),
        ("/Settlements/ReadDetailByCriteria", {"settlementID": 3071763}),
        ("/SettlementDetail/ReadByCriteria", {"settlementID": 3071763}),
        ("/Settlements/ReadCarriagesByCriteria", {"settlementID": 3071763}),
        ("/Settlements/ReadCartasByCriteria", {"settlementID": 3071763}),
        ("/SettlementCarriage/ReadByCriteria", {"settlementID": 3071763}),
    ]
    for ep, body in detalle_endpoints:
        try:
            r = page.context.request.post(f"{API}{ep}", headers=H, data=json.dumps(body), timeout=30000)
            t = r.text()
            n = 0
            try:
                j = json.loads(t)
                if isinstance(j, list): n = len(j)
                elif isinstance(j, dict) and "List" in j: n = len(j["List"]) if isinstance(j["List"], list) else 0
            except: pass
            print(f"  [{r.status:3d}] {ep[:50]:50s} -> {n} items / {t[:100]}")
            if r.status == 200 and n > 0:
                safe_ep = ep.replace("/","_").strip("_")
                (OUT/f"probe_{safe_ep}.json").write_text(t, encoding="utf-8")
        except Exception as e: pass

    ctx.close()
