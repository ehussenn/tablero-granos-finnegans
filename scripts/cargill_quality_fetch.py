"""Baja CALIDAD + SERVICIOS/FLETES por descarga de Cargill.
Endpoint: /v1/movements/{legalDocument}?...&isSummary=true&key={documentsType}
Devuelve data.movementsDetail con qualityAnalysis[] y services[].
Guarda en data/cargill/quality.json keyed por CTG. Incremental.
"""
import sys, json, re, time
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "scraper" / ".cargill_profile"
DATA = ROOT.parent / "data" / "cargill"
CUSTOMER_ID = "35188546"
API = "https://api.cglcloud.com/api/dxo/gps"

def ctg_of(legal):
    return str(legal or "").split("-")[-1]

def main():
    movements = json.loads((DATA/"movements.json").read_text(encoding="utf-8"))
    qfile = DATA/"quality.json"
    quality = {}
    if qfile.exists():
        try: quality = json.loads(qfile.read_text(encoding="utf-8"))
        except: quality = {}
    # CTGs de balanza (para priorizar los relevantes)
    bctgs = set()
    idx = ROOT.parent/"index.html"
    if idx.exists():
        h = idx.read_text(encoding="utf-8"); i = h.find("const PAYLOAD = ")
        if i>=0:
            import json as J
            obj,_ = J.JSONDecoder().raw_decode(h, i+len("const PAYLOAD = "))
            bctgs = set(str(r.get("ctg")) for r in obj.get("finales",[]) if r.get("ctg"))
    # objetivo: movimientos cuyo CTG está en balanza y aún no tenemos quality
    targets = []
    for m in movements:
        c = ctg_of(m.get("legalDocument"))
        if c and c in bctgs and c not in quality and m.get("legalDocument") and m.get("documentsType"):
            targets.append(m)
    print(f"[+] movements={len(movements)} | match balanza sin quality={len(targets)} | ya tengo={len(quality)}")
    if not targets:
        print("[+] nada nuevo para bajar"); return 0

    TOKEN=[None]
    def on_req(r):
        if "api.cglcloud.com" in r.url and r.method=="GET":
            a=r.headers.get("authorization")
            if a and not TOKEN[0]: TOKEN[0]=a
    with sync_playwright() as p:
        ctx=p.chromium.launch_persistent_context(user_data_dir=str(PROFILE),headless=True,
            viewport={"width":1400,"height":900},args=["--disable-blink-features=AutomationControlled"])
        page=ctx.pages[0] if ctx.pages else ctx.new_page()
        page.on("request",on_req)
        page.goto("https://www.mycargill.com/cascsa/v2/app/Movements",wait_until="domcontentloaded",timeout=60000)
        page.wait_for_timeout(9000)
        if "/login" in page.url or not TOKEN[0]:
            print("[X] sin sesion/token (re-logueá con cargill_daily_refresh)"); ctx.close(); return 1
        H={"authorization":TOKEN[0],"accept":"application/json"}
        ok=0
        for n,m in enumerate(targets,1):
            legal=m["legalDocument"]; key=m["documentsType"]; c=ctg_of(legal)
            url=f"{API}/v1/movements/{legal}?source=JDEAR&role=DXP_GPS_Role_Client&customerId={CUSTOMER_ID}&isSummary=true&key={key}"
            try:
                resp=page.request.get(url,headers=H,timeout=30000)
                d=resp.json().get("data",{}).get("movementsDetail",{}) if resp.ok else {}
                qa={}
                for q in d.get("qualityAnalysis",[]):
                    val=q.get("valueCargill")   # <- el valor real está acá
                    try: val=float(str(val).replace(",","."))
                    except: pass
                    at=str(q.get("analysisType","")).replace("#","Ñ")  # ñ corrupta
                    qa[at]={"valor":val,"unidad":q.get("analysisUnit"),"mermaKg":q.get("discount")}
                svc=[{"servicio":s.get("serviceName"),"precio":s.get("unitPrice"),"calculo":s.get("calculationType"),
                      "facturado":s.get("billed"),"moneda":s.get("currencyCode")} for s in d.get("services",[])]
                quality[c]={
                    "ctg":c,"movementNumber":d.get("movementNumber"),"producto":d.get("productName"),
                    "grado":d.get("cargillGrade"),"analisisId":d.get("cargillAnalysisId"),
                    "totalDiscount":d.get("totalDiscount"),"destino":d.get("destination"),
                    "fechaDescarga":d.get("deliveryDate"),"calidad":qa,"servicios":svc,
                }
                ok+=1
            except Exception as e:
                if n<=3: print(f"   err {c}: {str(e)[:60]}")
            if n%25==0:
                print(f"   ...{n}/{len(targets)}"); qfile.write_text(json.dumps(quality,ensure_ascii=False),encoding="utf-8")
            time.sleep(0.15)
        qfile.write_text(json.dumps(quality,ensure_ascii=False),encoding="utf-8")
        print(f"[+] OK {ok}/{len(targets)} bajados. Total quality.json: {len(quality)}")
        ctx.close()
    return 0

if __name__=="__main__":
    sys.exit(main())
