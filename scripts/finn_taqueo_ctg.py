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
sys.path.insert(0, str(Path(__file__).resolve().parent))
import finnegans_api as api   # para el mapeo de consignación (compra CV)

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

    # ---- Segunda pasada: CONSIGNACIÓN (CV) ----
    # Un CTG de consignación entra por compra CV y sale por venta CV; se liquida con
    # LiquidacionGranosCompraVO en el contrato de COMPRA, no en la grilla de liquidaciones
    # del contrato de venta. Sin esto, todos los CTG de consignación dan falso "sin liquidar".
    try:
        flag = set()
        for r in results:
            flag |= set(r.get("sin_liquidar_ctg", []))
        print(f"\n[CV] consignación: chequeando {len(flag)} CTG flagueados contra liquidaciones de compra…")
        # ctg -> nº contrato de compra CV (desde trasladoGranos)
        tr = api.call("/reports/trasladoGranos", {"PARAMFechaDesde": "2025-01-01", "PARAMFechaHasta": "2027-12-31"}, timeout=280)
        tr = tr if isinstance(tr, list) else []
        cv = {}
        for r in tr:
            ctg = norm(r.get("NUMERODOCUMENTOADICIONAL"))
            if ctg in flag and r.get("OPERACIONTIPO") == "Compra" and "COMPRA CV" in (r.get("TRANSACCIONSUBTIPONOMBRE") or ""):
                m = re.search(r"-\s*(\d+)", str(r.get("NOMBRECONTRATO") or r.get("NUMERODOCUMENTOCONTRATO") or ""))
                if m: cv[ctg] = m.group(1)
        # nº contrato compra -> CONTRATOID (desde ResumenContratoCompraGranos)
        cp = api.call("/reports/ResumenContratoCompraGranos", {"PARAMWEBREPORT_FechaDesde": "2022-01-01", "PARAMWEBREPORT_FechaHasta": "2030-12-31"}, timeout=200)
        cp = cp if isinstance(cp, list) else []
        num2cid = {}
        for c in cp:
            ni = str(c.get("numerointerno") or c.get("NUMEROINTERNO") or "")
            cid = c.get("contratoid") or c.get("CONTRATOID")
            if ni and cid: num2cid.setdefault(ni, str(cid))
        contratos_cv = sorted(set(v for v in cv.values() if v))
        print(f"[CV] {len(cv)} CTG consignación · {len(contratos_cv)} contratos de compra a revisar")
        liq_compra = set()
        for k, ncp in enumerate(contratos_cv, 1):
            cid = num2cid.get(ncp)
            if not cid: continue
            try:
                lx = post("getAjaxResponseForGrillaRefreshLiquidaciones", cid, LIQ_CAP, LIQ_NAM)
                for pk in [row.split(',')[0].strip() for row in cdata_rows(lx) if row.split(',')[0].strip().isdigit()]:
                    d = req(lambda: page.request.get(f"{BSA}/standardDF_def_and_data?standardXml=claseVO=LiquidacionGranosCompraVO&pk={pk}&fromDFVIEWER=1", timeout=60000).text())
                    liq_compra |= set(norm(x) for x in re.findall(r'<Descripcion[^>]*>(\d{11})/', d))
            except Exception as e:
                print(f"   [CV {k}/{len(contratos_cv)}] compra {ncp}: {str(e)[:40]}")
            if k % 10 == 0: print(f"   [CV] {k}/{len(contratos_cv)} · liquidados compra acum {len(liq_compra)}")
        quit = 0
        for r in results:
            if "sin_liquidar_ctg" not in r: continue
            keep = [c for c in r["sin_liquidar_ctg"] if c not in liq_compra]
            quit += len(r["sin_liquidar_ctg"]) - len(keep)
            r["sin_liquidar_ctg"] = keep
            if "detalle" in r:
                r["detalle"] = [x for x in r["detalle"] if x["ctg"] in keep or not x.get("ok_11d", True)]
            r["tn_sin_liquidar"] = round(sum(x["tn"] for x in r.get("detalle", []) if x["ctg"] in keep), 2)
        print(f"[CV] liquidados en compra: {len(liq_compra)} · falsos positivos quitados: {quit}")
    except Exception as e:
        print(f"[CV] error en pasada de consignación (se deja sin corregir): {str(e)[:100]}")

    b.close()
OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
tot = sum(len(r.get("sin_liquidar_ctg", [])) for r in results)
tn = round(sum(r.get("tn_sin_liquidar", 0) for r in results), 1)
print(f"\n[+] LISTO · {len(results)} contratos · {tot} CTG sin liquidar · {tn} tn · guardado {OUT}")
