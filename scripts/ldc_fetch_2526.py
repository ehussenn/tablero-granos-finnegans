"""Baja LDC campaña 25/26 (HarvestID=55) para soja(23)+maíz(2) vía
ApplicationsQuality/ReadByCriteria. Guarda data/ldc/quality.json keyed por CTG."""
import sys, json
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "scraper" / ".ldc_profile"
DATA = ROOT.parent / "data" / "ldc"; DATA.mkdir(parents=True, exist_ok=True)
EP = "https://mildc.com/Dreyfus.Extranet.Site.UI.Services/api/ApplicationsQuality/ReadByCriteria"
PRODUCTS = {23: "SOJA", 2: "MAIZ"}   # HarvestID 55 = cosecha 2025/2026

def main():
    out = {}
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(user_data_dir=str(PROFILE), headless=True,
            viewport={"width":1400,"height":900}, args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://mildc.com/webportal", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(7000)
        if "login" in page.url.lower():
            print("[X] sesion vencida"); ctx.close(); return 1
        # token anti-forgery (Angular: cookie XSRF-TOKEN -> header X-XSRF-TOKEN)
        xsrf = None
        for c in ctx.cookies():
            if c.get("name","").upper() in ("XSRF-TOKEN","X-XSRF-TOKEN"):
                import urllib.parse; xsrf = urllib.parse.unquote(c.get("value","")); break
        print("[+] XSRF:", "sí" if xsrf else "no")
        for pid, pname in PRODUCTS.items():
            body = {"CounterpartID": None, "DateFrom": "2026-01-01T00:00:00.000Z",
                    "DateUntil": "2026-12-31T23:59:59.000Z", "ProductID": pid,
                    "HarvestID": 55, "ConditionalKey": "GroupC"}
            hdr = {"content-type": "application/json", "accept": "application/json"}
            if xsrf: hdr["X-XSRF-TOKEN"] = xsrf
            try:
                r = page.request.post(EP, data=json.dumps(body), headers=hdr, timeout=90000)
                data = r.json() if r.ok else {}
                lst = data.get("List") if isinstance(data, dict) else (data if isinstance(data, list) else [])
                print(f"[+] {pname} (pid {pid}): status {r.status} -> {len(lst or [])} aplicaciones")
                for it in lst or []:
                    ctg = str(it.get("CTG") or "").strip()
                    if not ctg: continue
                    cal = {q.get("HeadingName"): q.get("HeadingValue") for q in (it.get("QualityList") or [])}
                    out[ctg] = {
                        "ctg": ctg, "contrato": it.get("ContractNumber"), "producto": it.get("ProductName"),
                        "factor": it.get("Factor"), "grado": it.get("GradeChamberID"),
                        "humedadReduccion": it.get("HumidityReduction"), "zarandaReduccion": it.get("SieveReduction"),
                        "pagaFlete": it.get("PaidFreight"), "netWeight": it.get("NetWeight"),
                        "pesoAcondicionado": it.get("ConditioningWeight"),
                        "f1116": it.get("F1116APrePrintNumber"), "calidad": cal,
                    }
            except Exception as e:
                print(f"[!] {pname} err: {str(e)[:80]}")
        ctx.close()
    (DATA/"quality.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"[+] data/ldc/quality.json: {len(out)} CTGs")
    from collections import Counter
    print("   por producto:", Counter(v["producto"] for v in out.values()).most_common())
    return 0

if __name__ == "__main__":
    sys.exit(main())
