"""Scraper LDC autónomo: login + Aplicaciones + busca SOJA y MAIZ cosecha 25/26
y captura la respuesta ApplicationsQuality. Guarda data/ldc/quality.json por CTG.
Reutilizable para el refresco automático."""
import sys, json, re
from pathlib import Path
from playwright.sync_api import sync_playwright
from _env import need
sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "scraper" / ".ldc_profile"
DATA = ROOT.parent / "data" / "ldc"; DATA.mkdir(parents=True, exist_ok=True)
USER, PWD = need("LDC_USER"), need("LDC_PASS")
RESP = []   # respuestas ApplicationsQuality

def main(headless=True):
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(user_data_dir=str(PROFILE), headless=headless,
            viewport={"width":1550,"height":950}, args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        def on_resp(r):
            if "ApplicationsQuality/ReadByCriteria" in r.url and r.request.method=="POST":
                try:
                    if "json" in r.headers.get("content-type",""): RESP.append(r.json())
                except Exception: pass
        page.on("response", on_resp)
        page.goto("https://mildc.com/webportal", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(7000)
        if page.locator("input[type=password]").count() > 0:
            print("[+] login")
            page.locator("input[type=email], input[type=text]").first.fill(USER)
            page.locator("input[type=password]").first.fill(PWD)
            for s in ["button:has-text('Iniciar')","button[type=submit]"]:
                if page.locator(s).count()>0: page.locator(s).first.click(); break
            page.wait_for_timeout(8000)
        # Aplicaciones
        for s in ["text=Aplicaciones","[role=tab]:has-text('Aplicaciones')"]:
            try:
                if page.locator(s).count()>0: page.locator(s).first.click(timeout=4000); break
            except Exception: pass
        page.wait_for_timeout(4000)

        def pick_combo(label, typed):
            """Abre el combobox cuyo label contiene `label` y tipea+selecciona `typed`."""
            try:
                cb = page.locator(f"xpath=//label[contains(normalize-space(.),'{label}')]/following::input[@id='comboBox'][1]").first
                cb.scroll_into_view_if_needed(); cb.click(timeout=4000); page.wait_for_timeout(400)
                page.keyboard.press("Control+A"); page.keyboard.press("Delete")
                page.keyboard.type(str(typed), delay=60); page.wait_for_timeout(1100)
                page.keyboard.press("ArrowDown"); page.wait_for_timeout(250)
                page.keyboard.press("Enter"); page.wait_for_timeout(600); return True
            except Exception as e:
                print(f"   [!] combo {label}={typed}: {str(e)[:70]}"); return False

        for prod in ["SOJA", "MAIZ"]:
            print(f"[+] Buscando {prod} 25/26...")
            pick_combo("Producto", prod)
            pick_combo("Cosecha", "2025")
            try:
                d = page.get_by_placeholder("DD/MM/YYYY").first
                d.click(); d.fill(""); d.type("01/01/2026", delay=30)
            except Exception as e: print("   [!] fecha:", str(e)[:50])
            n0 = len(RESP)
            try: page.get_by_role("button", name=re.compile("Buscar", re.I)).first.click(timeout=4000)
            except Exception as e: print("   [!] buscar:", str(e)[:50])
            for _ in range(20):
                page.wait_for_timeout(700)
                if len(RESP) > n0: break
            print(f"   -> respuestas acumuladas: {len(RESP)}")
        ctx.close()

    # parsear todas las respuestas (MERGEANDO sobre lo ya guardado)
    out = {}
    qf = DATA/"quality.json"
    if qf.exists():
        try: out = json.loads(qf.read_text(encoding="utf-8"))
        except Exception: out = {}
    for d in RESP:
        lst = d.get("List") if isinstance(d, dict) else (d if isinstance(d, list) else [])
        for it in lst or []:
            ctg = str(it.get("CTG") or "").strip()
            if not ctg: continue
            cal = {q.get("HeadingName"): q.get("HeadingValue") for q in (it.get("QualityList") or [])}
            out[ctg] = {"ctg":ctg,"contrato":it.get("ContractNumber"),"producto":it.get("ProductName"),
                "factor":it.get("Factor"),"grado":it.get("GradeChamberID"),
                "humedadReduccion":it.get("HumidityReduction"),"pagaFlete":it.get("PaidFreight"),
                "netWeight":it.get("NetWeight"),"pesoAcondicionado":it.get("ConditioningWeight"),
                "f1116":it.get("F1116APrePrintNumber"),"calidad":cal}
    (DATA/"quality.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    from collections import Counter
    print(f"[+] data/ldc/quality.json: {len(out)} CTGs | {dict(Counter(v['producto'] for v in out.values()))}")
    return 0

if __name__ == "__main__":
    sys.exit(main(headless=("--visible" not in sys.argv)))
