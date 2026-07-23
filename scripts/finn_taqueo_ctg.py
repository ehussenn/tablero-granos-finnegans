"""TAQUEO CTG automatizado vía Finnegans GO (BSA, sesión CDP 9340).
Por cada contrato de venta (CONTRATOID) con entregado pendiente de liquidar:
  - grid Entregas    -> CTG entregados (+ tn Peso Neto)
  - grid Liquidaciones -> pk de cada liquidación -> detalle -> CTG liquidados (COE con traslado)
  - diff = CTG entregado SIN LIQUIDAR
Guardado incremental con resume. Uso: py scripts/finn_taqueo_ctg.py [targets.json]
"""
import sys, re, json, time
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")

SN = Path("scripts/scraper/out/finn_sniff")
TARGETS = Path(sys.argv[1]) if len(sys.argv) > 1 else SN / "_all_targets.json"
OUT = SN / "_taqueo_all.json"
BSA = "https://oneteam.finneg.com/BSA"
GRID_URL = BSA + "/webreport/data?layout=WebReportGridLayout&masterWR=0&custom=1&clazz=faf.client.ui.WidgetGrid2&method=dataMethod&"

ENT_CAP = "TransaccionID|Ticket|Carta Porte|CTG|Fecha|Cant. Vinculada|Peso Neto s/Mermas|% Humedad|Merma Secado|% Zaranda|Merma Zaranda|% Volatil|Merma Volatil|Otras Mermas|Diferencia de balanza|Peso Neto|Grado|Factor"
ENT_NAM = "[TransaccionID]|Ticket|CartaPorte|CTG|Fecha|CantidadVinculada|PesoBruto|PorcHumedad|MermaSecado|PorcZaranda|MermaZaranda|PorcVolatil|MermaVolatil|OtrasMermas|DifBalanza|PesoReal|Grado|Factor"
LIQ_CAP = "TransaccionID|Numero Interno|Nro.Comprobante|Fecha Comprobante|Tipo|CAC|Fecha|Cantidad|Precio Mon.Contrato|Bruto Mon.Contrato|Otros Mon.Contrato|Total Mon.Contrato|Precio Mon. Liq|Bruto Mon.Liq|Otros Mon.Liq|Total Mon.Liq"
LIQ_NAM = "[TransaccionID]|Numero|Documento|FechaComprobante|Tipo|CAC|Fecha|CantidadGrano|PrecioGranoMonContrato|BrutoMonContrato|OtrosMonContrato|TotalMonContrato|PrecioGrano|Bruto|Otros|Total"


def grid_body(method, pk, cap, nam):
    return ('<?xml version="1.0" encoding="UTF-8"?><postData><userData><![CDATA[pkField=TransaccionID\n'
            f'captions={cap}\nnames={nam}\n'
            'class=app.ceres.transacciones.comercializacion.contratos.granos.ContratoGranosHLP\n'
            f'method={method}\npk={pk}\n]]></userData><parameters></parameters></postData>')


def norm(c):
    c = re.sub(r'\D', '', str(c or '')); return c.lstrip('0')


def cdata_rows(xml):
    m = re.search(r'<data><!\[CDATA\[(.*?)\]\]>', xml, re.S)
    return [r for r in m.group(1).split(';') if r.strip()] if m else []


def contnum(s):
    m = re.search(r'-\s*(\d+)\(', str(s or '')); return m.group(1) if m else None


targets = json.loads(TARGETS.read_text(encoding="utf-8"))
done = {}
if OUT.exists():
    try:
        done = {d["CONTRATOID"]: d for d in json.loads(OUT.read_text(encoding="utf-8"))}
    except Exception:
        done = {}
print(f"[i] {len(targets)} contratos objetivo · {len(done)} ya procesados (resume)")

results = list(done.values())
with sync_playwright() as p:
    b = p.chromium.connect_over_cdp("http://localhost:9340")
    page = b.contexts[0].pages[0]
    hdr = {"content-type": "application/x-www-form-urlencoded; charset=UTF-8"}

    def req(fn, *a, tries=3):
        for k in range(tries):
            try:
                return fn(*a)
            except Exception as e:
                if k == tries - 1:
                    raise
                time.sleep(1.5 * (k + 1))

    def post(method, pk, cap, nam):
        return req(lambda: page.request.post(GRID_URL, data=grid_body(method, pk, cap, nam), headers=hdr, timeout=60000).text())

    def liq_detail(pk):
        u = f"{BSA}/standardDF_def_and_data?standardXml=claseVO=LiquidacionGranosVentaVO&pk={pk}&fromDFVIEWER=1"
        return req(lambda: page.request.get(u, timeout=60000).text())

    for idx, t in enumerate(targets, 1):
        cid = t["CONTRATOID"]
        if cid in done:
            continue
        num = contnum(t["CONTRATO"]) or cid
        try:
            ent_xml = post("getAjaxResponseForGrillaRefreshEntregas", cid, ENT_CAP, ENT_NAM)
            entregas = {}
            for row in cdata_rows(ent_xml):
                f = row.split(',')
                if len(f) > 16:
                    ctg = norm(f[4])
                    try: peso = float(f[16])
                    except: peso = 0.0
                    if not peso:
                        try: peso = float(f[6])
                        except: peso = 0.0
                    if ctg:
                        entregas[ctg] = {"tn": peso, "cp": f[3], "fecha": f[5]}
            liq_xml = post("getAjaxResponseForGrillaRefreshLiquidaciones", cid, LIQ_CAP, LIQ_NAM)
            liq_pks = [r.split(',')[0].strip() for r in cdata_rows(liq_xml) if r.split(',')[0].strip().isdigit()]
            liquidados = set()
            for pk in liq_pks:
                d = liq_detail(pk)
                liquidados |= set(norm(x) for x in re.findall(r'<Descripcion[^>]*>(\d{11})/', d))
            sin_liq = sorted(set(entregas) - liquidados)
            det = [{"ctg": c, "tn": round(entregas[c]["tn"], 2), "cp": entregas[c]["cp"],
                    "fecha": entregas[c]["fecha"], "ok_11d": len(c) == 11} for c in sin_liq]
            tn_sin = round(sum(entregas[c]["tn"] for c in sin_liq if len(c) == 11), 2)
            rec = {"contrato": num, "CONTRATOID": cid, "cer": t.get("cer"), "org": t.get("org"),
                   "prod": t.get("prod"), "cos": t.get("cos"), "pend_grid": t.get("pend"),
                   "entregas_ctg": len(entregas), "tn_entregado": round(sum(v["tn"] for v in entregas.values()), 2),
                   "liq_count": len(liq_pks), "liquidados_ctg": len(liquidados & set(entregas)),
                   "sin_liquidar_ctg": [c for c in sin_liq if len(c) == 11],
                   "sin_liquidar_raras": [c for c in sin_liq if len(c) != 11],
                   "tn_sin_liquidar": tn_sin, "detalle": det}
        except Exception as e:
            rec = {"contrato": num, "CONTRATOID": cid, "cer": t.get("cer"), "error": str(e)[:120]}
            print(f"  [{idx}/{len(targets)}] ctto {num} ERROR: {str(e)[:80]}")
        results.append(rec)
        done[cid] = rec
        if "error" not in rec:
            print(f"  [{idx}/{len(targets)}] {rec['cer'][:14]:14} ctto {num}: ent={rec['entregas_ctg']} liq={rec['liq_count']} -> SIN LIQ={len(rec['sin_liquidar_ctg'])} ({rec['tn_sin_liquidar']}tn)" + (f" ⚠{len(rec['sin_liquidar_raras'])} raras" if rec['sin_liquidar_raras'] else ""))
        if idx % 5 == 0 or idx == len(targets):
            OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    b.close()
OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
tot = sum(len(r.get("sin_liquidar_ctg", [])) for r in results)
tn = round(sum(r.get("tn_sin_liquidar", 0) for r in results), 1)
print(f"\n[+] LISTO · {len(results)} contratos · {tot} CTG sin liquidar · {tn} tn · guardado {OUT}")
