"""Debug rápido: dump JSON crudo de movements, payments, invoices para ver estructura real."""
import sys, json
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "scraper" / ".cargill_profile"
OUT = ROOT / "scraper" / "out"
OUT.mkdir(parents=True, exist_ok=True)

CUSTOMER_ID = "35188546"
API = "https://api.cglcloud.com/api/dxo/gps"
TOKEN = None

def on_req(r):
    global TOKEN
    if "api.cglcloud.com" in r.url and r.method == "GET":
        a = r.headers.get("authorization")
        if a and not TOKEN: TOKEN = a

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(user_data_dir=str(PROFILE), headless=False, viewport={"width":1500,"height":950})
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.on("request", on_req)
    page.goto("https://www.mycargill.com/cascsa/v2/app/Movements", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(8000)
    if not TOKEN: print("[!] no token"); ctx.close(); sys.exit(1)
    print(f"[+] Token: {TOKEN[:50]}...")

    H = {"authorization": TOKEN, "accept":"application/json"}

    for ep, args in [
        ("/v1/movements", {"sortBy":"loadUnloadDate","sort":"desc","offset":0,"limit":3,"legalDocument":""}),
        ("/v1/payments",  {"offset":0,"limit":3}),
        ("/v1/invoices",  {"offset":0,"limit":3}),
    ]:
        qs = "&".join(f"{k}={v}" for k, v in {**args, "customerId":CUSTOMER_ID,"source":"JDEAR","role":"DXP_GPS_Role_Client"}.items())
        url = f"{API}{ep}?{qs}"
        print(f"\n{'='*80}\n{ep}\n{'='*80}")
        try:
            r = page.context.request.get(url, headers=H, timeout=20000)
            print(f"  status: {r.status}")
            j = r.json()
            print(f"  TIPO: {type(j).__name__}")
            if isinstance(j, dict):
                print(f"  KEYS: {list(j.keys())}")
                for k in j.keys():
                    v = j[k]
                    if isinstance(v, list) and v:
                        print(f"\n  → {k} (list de {len(v)}):")
                        if isinstance(v[0], dict):
                            print(f"     COLS: {list(v[0].keys())}")
                            print(f"     Fila[0] (campos no vacios):")
                            for kk, vv in v[0].items():
                                if vv not in (None, "", 0, "0", "0.0"):
                                    print(f"       {kk:<35} = {repr(vv)[:80]}")
                        else:
                            print(f"     sample: {repr(v[0])[:120]}")
                    elif isinstance(v, dict):
                        print(f"\n  → {k} (dict): keys={list(v.keys())[:10]}")
                    else:
                        print(f"  → {k}: {repr(v)[:80]}")
            elif isinstance(j, list):
                print(f"  LISTA de {len(j)}")
                if j: print(f"  primer item: {repr(j[0])[:200]}")
        except Exception as e:
            print(f"  ERR: {e}")
    ctx.close()
