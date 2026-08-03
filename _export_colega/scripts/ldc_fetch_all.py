"""Baja TODO de LDC: Settlements + Fixations + Movements + Applications desde 01/01/2026.
Usa el Bearer token capturado por ldc_auto_explore. Refresh-able."""
from __future__ import annotations
import sys, os, json, time
from pathlib import Path
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / "scripts" / "scraper" / ".ldc_profile"
OUT = ROOT / "data" / "ldc"
OUT.mkdir(parents=True, exist_ok=True)

env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

USER = os.environ.get("LDC_USER", "<EXTRANET_USER_EMAIL>")
PWD = os.environ.get("LDC_PASS", "<PASSWORD>")
CUIT = "30710712758"  # Agronasaja
DATE_FROM_ISO = "2026-01-01T00:00:00.000Z"
DATE_UNTIL_ISO = datetime.now().strftime("%Y-%m-%dT23:59:59.999Z")
DATE_FROM_DDMM = "01/01/2026"
DATE_UNTIL_DDMM = datetime.now().strftime("%d/%m/%Y")
API = "https://mildc.com/Dreyfus.Extranet.Site.UI.Services/api"
TOKEN = [None]

def on_req(r):
    if "mildc.com" in r.url:
        a = r.headers.get("authorization", "")
        if a and "bearer" in a.lower() and not TOKEN[0]: TOKEN[0] = a

from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE), headless=False,
        viewport={"width":1500,"height":950},
        args=["--disable-blink-features=AutomationControlled"],
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.on("request", on_req)

    print("[+] Abriendo portal LDC...")
    page.goto("https://mildc.com/webportal", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(8000)
    if page.locator("input[type='password']").count() > 0:
        print("[+] Login...")
        page.locator("input[type='text'], input[type='email']").first.fill(USER)
        page.locator("input[type='password']").first.fill(PWD)
        page.locator("button:has-text('Iniciar sesión'), button[type='submit']").first.click()
        page.wait_for_timeout(12000)
    page.goto("https://mildc.com/webportal/dashboard", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(8000)

    if not TOKEN[0]:
        print("[!] no se capturo token, abortando")
        ctx.close(); sys.exit(1)
    print(f"[+] Token OK")

    H = {"authorization": TOKEN[0], "accept": "application/json", "content-type": "application/json"}

    # Helper para hacer POSTs
    def post(endpoint, body, label=None):
        url = f"{API}{endpoint}"
        label = label or endpoint.split("/")[-1]
        try:
            r = page.context.request.post(url, headers=H, data=json.dumps(body), timeout=60000)
            ct = r.headers.get("content-type", "")
            txt = r.text()
            if r.status == 200 and "json" in ct.lower():
                try: j = r.json()
                except: j = {"_raw": txt[:500]}
                size = len(json.dumps(j)) if isinstance(j, (dict, list)) else len(txt)
                # contar items si es lista o tiene 'List'
                items = j if isinstance(j, list) else (j.get("List") if isinstance(j, dict) else None)
                cnt = len(items) if isinstance(items, list) else "?"
                print(f"   [{r.status}] {label:35s} {cnt} items / {size}b")
                return j
            else:
                print(f"   [{r.status}] {label:35s} ERR — {txt[:140]}")
                return None
        except Exception as e:
            print(f"   [!] {label}: {str(e)[:120]}")
            return None

    print("\n[+] LIQUIDACIONES (Settlements)...")
    s = post("/Settlements/ReadByCriteriaSettled", {
        "ConditionalKey": "GroupD",
        "IssueDateFrom": DATE_FROM_DDMM,
        "IssueDateUntil": DATE_UNTIL_DDMM,
        "CompanyDocumentNumber": CUIT,
    }, "Settlements")
    if s: (OUT/"settlements.json").write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")

    # Probar variantes
    for variant in [
        {"ConditionalKey":"GroupD","SocietyID":None,"DateFrom":None,"DateUntil":None,"ContractNumber":None,"DocumentNumber":None,
         "IssueDateFrom":DATE_FROM_DDMM,"IssueDateUntil":DATE_UNTIL_DDMM,"CompanyDocumentNumber":CUIT,"isMassiveSearch":True},
    ]:
        s2 = post("/Settlements/ReadByCriteriaSettled", variant, "Settlements-v2")
        if s2: (OUT/"settlements_v2.json").write_text(json.dumps(s2, ensure_ascii=False, indent=2), encoding="utf-8"); break

    print("\n[+] FIJACIONES (Fixations)...")
    f = post("/Fixations/ReadByCriteria", {
        "ConditionalKey": "GroupB",
        "DateFrom": DATE_FROM_ISO,
        "DateUntil": DATE_UNTIL_ISO,
        "CompanyDocumentNumber": CUIT,
        "isMassiveSearch": True,
    }, "Fixations")
    if f: (OUT/"fixations.json").write_text(json.dumps(f, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n[+] APLICACIONES (ApplicationsQuality)...")
    a = post("/ApplicationsQuality/ReadByCriteria", {
        "ConditionalKey": "GroupB",
        "DateFrom": DATE_FROM_ISO,
        "DateUntil": DATE_UNTIL_ISO,
        "CompanyDocumentNumber": CUIT,
        "isMassiveSearch": True,
    }, "ApplicationsQuality")
    if a: (OUT/"applications_quality.json").write_text(json.dumps(a, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n[+] MOVIMIENTOS (MovementsQuantity)...")
    m = post("/MovementsQuantity/ReadByCriteria", {
        "ConditionalKey": "GroupB",
        "DateFrom": DATE_FROM_ISO,
        "DateUntil": DATE_UNTIL_ISO,
        "CompanyDocumentNumber": CUIT,
        "isMassiveSearch": True,
    }, "MovementsQuantity")
    if m: (OUT/"movements_quantity.json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")

    # Probar otros endpoints utiles
    print("\n[+] NotAppliedProduct...")
    n = post("/NotAppliedProduct/ListTotalsByProduct", {
        "ConditionalKey": "GroupB",
        "DateFrom": DATE_FROM_ISO,
        "DateUntil": DATE_UNTIL_ISO,
        "CounterpartID": None,
    }, "NotAppliedProduct")
    if n: (OUT/"not_applied_product.json").write_text(json.dumps(n, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n[+] CuentaCorriente paid to date...")
    cc = post("/CurrentAccountsHist/ReadIndicatorPaidToDate", {
        "SocietyCode":"AR02","CountryCode":"AR",
        "DateFrom": DATE_FROM_ISO, "DateUntil": DATE_UNTIL_ISO,
        "Cuit": CUIT,
    }, "PaidToDate")
    if cc: (OUT/"paid_to_date.json").write_text(json.dumps(cc, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n[+] Done.")
    ctx.close()
