"""Baja calidad+factor+flete por CTG de LDC (ApplicationsQuality/ReadByCriteria).
Captura el body del request (al abrir Aplicaciones) y lo repite con rango amplio.
Guarda data/ldc/quality.json keyed por CTG."""
import sys, json, re
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "scraper" / ".ldc_profile"
DATA = ROOT.parent / "data" / "ldc"; DATA.mkdir(parents=True, exist_ok=True)
EP = "/api/ApplicationsQuality/ReadByCriteria"
cap = {"body": None, "headers": None, "url": None}

def main():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(user_data_dir=str(PROFILE), headless=True,
            viewport={"width":1500,"height":950}, args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        def on_req(r):
            if EP in r.url and r.method == "POST" and not cap["body"]:
                try:
                    cap["body"] = r.post_data; cap["url"] = r.url
                    cap["headers"] = {k: v for k, v in r.headers.items() if k.lower() in
                        ("content-type","authorization","x-xsrf-token","accept")}
                except Exception: pass
        page.on("request", on_req)
        page.goto("https://mildc.com/webportal", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(8000)
        if "login" in page.url.lower():
            print("[X] sesion vencida (logueá en el sniffer primero)"); ctx.close(); return 1
        # ir a Aplicaciones (dispara ApplicationsQuality)
        for sel in ["text=Aplicaciones", "a:has-text('Aplicaciones')", "button:has-text('Aplicaciones')",
                    "[role=tab]:has-text('Aplicaciones')"]:
            try:
                page.locator(sel).first.click(timeout=4000); break
            except Exception: pass
        page.wait_for_timeout(6000)
        if not cap["body"]:
            print("[X] no capturé el request de ApplicationsQuality (la búsqueda no se disparó)"); ctx.close(); return 1
        print("[+] body capturado:", str(cap["body"])[:200])
        # ampliar rango de fechas en el body
        try: body = json.loads(cap["body"])
        except Exception: body = {}
        for k in list(body.keys()):
            kl = k.lower()
            if "from" in kl and ("date" in kl or "fecha" in kl): body[k] = "20250101"
            if ("until" in kl or "to" in kl) and ("date" in kl or "fecha" in kl): body[k] = "20261231"
        H = dict(cap["headers"] or {}); H.setdefault("content-type","application/json")
        resp = page.request.post(cap["url"], data=json.dumps(body), headers=H, timeout=60000)
        print("[+] replay status:", resp.status)
        data = resp.json() if resp.ok else {}
        lst = data.get("List") if isinstance(data, dict) else (data if isinstance(data, list) else [])
        print(f"[+] aplicaciones con calidad: {len(lst)}")
        out = {}
        for r in lst or []:
            ctg = str(r.get("CTG") or "").strip()
            if not ctg: continue
            cal = {}
            for q in r.get("QualityList") or []:
                cal[q.get("HeadingName")] = q.get("HeadingValue")
            out[ctg] = {
                "ctg": ctg, "contrato": r.get("ContractNumber"), "producto": r.get("ProductName"),
                "factor": r.get("Factor"), "grado": r.get("GradeChamberID"),
                "humedadReduccion": r.get("HumidityReduction"), "zarandaReduccion": r.get("SieveReduction"),
                "pagaFlete": r.get("PaidFreight"), "netWeight": r.get("NetWeight"),
                "pesoAcondicionado": r.get("ConditioningWeight"),
                "f1116": r.get("F1116APrePrintNumber"), "calidad": cal,
            }
        (DATA/"quality.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        print(f"[+] guardado data/ldc/quality.json: {len(out)} CTGs")
        # muestra
        for c,v in list(out.items())[:3]:
            print(f"   CTG {c} {v['producto']} factor={v['factor']} grado={v['grado']} calidad={v['calidad']} flete={v['pagaFlete']}")
        ctx.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())
