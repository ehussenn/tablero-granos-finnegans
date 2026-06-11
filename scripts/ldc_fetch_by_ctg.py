"""Loop por los 349 CTGs LDC del DW. Por cada uno consulta:
- ApplicationsQuality (humedad/calidad)
- MovementsQuantity (movimientos)
Guarda en data/ldc/by_ctg/{CTG}.json los que tengan datos."""
from __future__ import annotations
import sys, os, json, time
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / "scripts" / "scraper" / ".ldc_profile"
OUT = ROOT / "data" / "ldc"
BY_CTG = OUT / "by_ctg"
BY_CTG.mkdir(parents=True, exist_ok=True)

env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

USER = os.environ.get("LDC_USER", "pgauto@agronasaja.com.ar")
PWD = os.environ.get("LDC_PASS", "Nasaja1234.")
API = "https://mildc.com/Dreyfus.Extranet.Site.UI.Services/api"
TOKEN = [None]

def on_req(r):
    if "mildc.com" in r.url:
        a = r.headers.get("authorization", "")
        if a and "bearer" in a.lower() and not TOKEN[0]: TOKEN[0] = a

# Leer CTGs del DW
ctgs_data = json.loads((OUT/"ldc_ctgs.json").read_text(encoding="utf-8"))
ctgs_unicos = sorted({str(c.get("numerodocumentoadicional")) for c in ctgs_data if c.get("numerodocumentoadicional")})
print(f"[+] {len(ctgs_unicos)} CTGs únicos a probar")

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
    print(f"[+] Token OK\n")

    H = {"authorization": TOKEN[0], "accept": "application/json", "content-type": "application/json"}
    summary = {"total": len(ctgs_unicos), "with_apps": 0, "with_movs": 0, "empty": 0}
    start = time.time()

    for i, ctg in enumerate(ctgs_unicos, 1):
        out_file = BY_CTG / f"{ctg}.json"
        if out_file.exists():
            try:
                existing = json.loads(out_file.read_text(encoding="utf-8"))
                if existing.get("apps") or existing.get("movs"):
                    summary["with_apps"] += 1 if existing.get("apps") else 0
                    summary["with_movs"] += 1 if existing.get("movs") else 0
                continue
            except: pass

        result = {"ctg": ctg, "apps": [], "movs": []}
        try:
            ra = page.context.request.post(f"{API}/ApplicationsQuality/ReadByCriteria",
                  headers=H, data=json.dumps({"CarriageDocumentNumber": ctg, "ConditionalKey":"GroupB"}),
                  timeout=20000)
            if ra.status == 200:
                j = ra.json()
                ls = j.get("ListSummary", {}).get("List") if isinstance(j, dict) else None
                if not ls and isinstance(j, dict): ls = j.get("List")
                if ls: result["apps"] = ls
        except: pass
        try:
            rm = page.context.request.post(f"{API}/MovementsQuantity/ReadByCriteria",
                  headers=H, data=json.dumps({"CarriageDocumentNumber": ctg, "ConditionalKey":"GroupB"}),
                  timeout=20000)
            if rm.status == 200:
                j = rm.json()
                ls = j.get("ListSummary", {}).get("List") if isinstance(j, dict) else None
                if not ls and isinstance(j, dict): ls = j.get("List")
                if ls: result["movs"] = ls
        except: pass

        if result["apps"] or result["movs"]:
            out_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            if result["apps"]: summary["with_apps"] += 1
            if result["movs"]: summary["with_movs"] += 1
        else:
            summary["empty"] += 1

        if i % 50 == 0 or i == len(ctgs_unicos):
            elapsed = time.time() - start
            rate = i/elapsed
            eta = (len(ctgs_unicos)-i)/rate
            print(f"  [{i}/{len(ctgs_unicos)}] apps={summary['with_apps']} movs={summary['with_movs']} empty={summary['empty']} | {rate:.1f}/s | ETA {eta/60:.1f}min")
        time.sleep(0.1)

    print(f"\n[+] FINAL: apps={summary['with_apps']} movs={summary['with_movs']} empty={summary['empty']}")
    (OUT/"by_ctg_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    ctx.close()
