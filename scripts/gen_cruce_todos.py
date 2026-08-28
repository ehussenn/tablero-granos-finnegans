# CRUCE camión x camión 25/26 para TODOS LOS CULTIVOS (generaliza el pipeline de trigo v4).
# Fuentes: series de liquidaciones cosechadas (data/liq_*.json) + reporte REST trasladoGranos
# (mapa doc->contrato/ctg/org/destino) + resumen de contratos REST (proveedores, convenios).
# USD: TC del día de la liquidación (tc_dol que trae cada liq). Sin Excel — solo el JSON del tablero.
import sys, json, pathlib, urllib.parse
from collections import defaultdict
sys.stdout.reconfigure(encoding="utf-8")

BASE = pathlib.Path(r"c:\Users\Public\Documents\Granos\tablero-granos-finnegans")
sys.path.insert(0, str(BASE / "scripts"))
import os
for line in (BASE / ".env").read_text(encoding="utf-8", errors="ignore").splitlines():
    line = line.strip()
    if "=" in line and not line.startswith("#"): k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip())
from finnegans_api import call

SCRATCH = pathlib.Path(__file__).resolve().parent
DATA = BASE / "data"
def num(v):
    try: return float(str(v).replace(",", "") or 0)
    except Exception: return 0.0

def cultivo_de(prod):
    p = str(prod or "").upper()
    if "TRIGO" in p: return "TRIGO"
    if "SOJA" in p or p.startswith("GSJ"): return "SOJA"
    if "MAIZ" in p or "MAÍZ" in p or p.startswith("GRM"): return "MAIZ"
    if "GIRASOL" in p: return "GIRASOL"
    if "SORGO" in p: return "SORGO"
    if "ARVEJA" in p: return "ARVEJA"
    if "CEBADA" in p: return "CEBADA"
    if "MANI" in p or "MANÍ" in p: return "MANI"
    if "AVENA" in p: return "AVENA"
    if "CENTENO" in p: return "CENTENO"
    if "CAMELINA" in p: return "CAMELINA"
    return None
def p2526(pr): return "25-26" in str(pr.get("partida") or "")

# ── resumen de contratos (compra y venta) — proveedores, COMP->nombre, convenios
RANGO = {"PARAMWEBREPORT_FechaDesde": "2022-01-01", "PARAMWEBREPORT_FechaHasta": "2030-12-31",
         "PARAMWEBREPORT_FechaEntregaMin": "2022-01-01", "PARAMWEBREPORT_FechaEntregaMax": "2030-12-31"}
def cosecha_de(r): return str(r.get("COSECHA") or r.get("CAMPANA") or "")
cp_res = [r for r in call("/reports/ResumenContratoCompraGranos", dict(RANGO))
          if cosecha_de(r) == "CAMPAÑA 25-26" and str(r.get("ESTADOANULACION") or "").strip().lower() != "anulado"]
vt_res = [r for r in call("/reports/resumenContratosVentaGranos", dict(RANGO))
          if cosecha_de(r) == "CAMPAÑA 25-26" and str(r.get("ESTADOANULACION") or "").strip().lower() != "anulado"]
print(f"resumen 25-26: {len(cp_res)} compra · {len(vt_res)} venta")
prov_por_cto, comp2nombre, fnn = {}, {}, {}
ctos_por_org = defaultdict(list)
for r in cp_res:
    n = str(r.get("NOMBRE") or "").strip()
    if not n: continue
    org = str(r.get("ORGANIZACION") or "").strip()
    prov_por_cto[n] = org
    ctos_por_org[org.upper()].append(n)
    fnn[n] = {"liquidada": num(r.get("CANTIDADLIQUIDADA")), "moneda": str(r.get("MONEDA") or ""),
              "precio_liq": num(r.get("PRECIOLIQUIDADO")), "fecha": str(r.get("FECHA") or "")[:10],
              "producto": str(r.get("PRODUCTO") or "")}
    comp = str(r.get("NUMERODOCUMENTO") or "").strip().upper().replace(" ", "")
    if comp: comp2nombre[comp] = n
HIS = set(fnn)
# venta: nro doc adicional -> contrato (fallback de asignación)
DOCADIC = {}
import re as _re
for r in vt_res:
    dig = _re.sub(r"\D", "", str(r.get("NUMERODOCUMENTOADICIONAL") or ""))
    if len(dig) >= 4:
        DOCADIC[dig] = {"cto": str(r.get("NOMBRE") or ""), "org": str(r.get("ORGANIZACION") or "")}

# ── mapa de traslados (REST): doc -> ctg/cto/org/dest, por lado
tras = call("/reports/trasladoGranos", {"PARAMFechaDesde": "2025-05-01", "PARAMFechaHasta": "2030-12-31"})
MAP = {"compra": {}, "venta": {}}
for r in tras:
    doc = str(r.get("DOCUMENTO") or "").strip()
    if not doc: continue
    sub = str(r.get("TRANSACCIONSUBTIPONOMBRE") or "")
    lado = "compra" if "COMPRA" in sub.upper() else ("venta" if "VENTA" in sub.upper() else None)
    ent = {"ctg": str(r.get("NUMERODOCUMENTOADICIONAL") or "").strip(),
           "cto": str(r.get("NOMBRECONTRATO") or "").strip(),
           "org": str(r.get("ORGANIZACIONNOMBRE") or "").strip(),
           "dest": str(r.get("DESTINATARIO") or "").strip()}
    if lado: MAP[lado][doc] = ent
    else: MAP.setdefault("otros", {})[doc] = ent
print(f"traslados: compra {len(MAP['compra'])} · venta {len(MAP['venta'])}")
def mapa(doc, lado):
    m = MAP.get(lado, {}).get(doc) or MAP.get("otros", {}).get(doc) or {}
    return {"cto": m.get("cto", ""), "ctg": m.get("ctg", ""), "org": m.get("org", ""), "dest": m.get("dest", "")}

CACHE_ORG = SCRATCH / "org_cache.json"
org_cache = json.loads(CACHE_ORG.read_text(encoding="utf-8")) if CACHE_ORG.exists() else {}
def org_nombre(pid):
    pid = str(pid or "")
    if not pid: return ""
    if pid not in org_cache:
        try:
            r = call(urllib.parse.quote(f"/organizacion/{pid}", safe="/"))
            org_cache[pid] = str(r.get("RazonSocial") or r.get("Nombre") or "")
        except Exception: org_cache[pid] = ""
        if len(org_cache) % 25 == 0: CACHE_ORG.write_text(json.dumps(org_cache, ensure_ascii=False), encoding="utf-8")
    return org_cache[pid]
def cto_por_prov(pid, cultivo):
    nom = org_nombre(pid).upper()
    if not nom: return "", ""
    cand = [c for c in ctos_por_org.get(nom, []) if cultivo_de(fnn.get(c, {}).get("producto")) == cultivo]
    if not cand:
        for o, cs in ctos_por_org.items():
            if o[:15] == nom[:15]:
                cand = [c for c in cs if cultivo_de(fnn.get(c, {}).get("producto")) == cultivo]; break
    if not cand: return "", nom
    return (cand[0] if len(cand) == 1 else " / ".join(sorted(set(cand)))), nom

SERIES_C = [("liq_compra_api.json", "LIQPRICPRA"), ("liq_cpragra_api.json", "LIQCPRAGRA"),
            ("liq_cprasem_api.json", "LIQCPRASEM")]
SERIES_V = [("liq_venta_pri_api.json", "LIQ-PRI-VTA"), ("liq_venta_sec_api.json", "LIQ-SEC-VTA"),
            ("liq_vta_int_api.json", "LIQ-VTA-INT"), ("liq_pri_vta_int_api.json", "LIQ-PRI-VTA-INT")]

# ── COMPRA: tramo por camión
compra_rows = []
for f, pref in SERIES_C:
    p = DATA / f
    if not p.exists(): continue
    for n, d in json.loads(p.read_text(encoding="utf-8")).items():
        nombre = f"{pref} - {n}"
        if str(d.get("fecha") or "") < "2025-09-01": continue
        con = d.get("conceptos") or {}
        lineas_val = [pr for pr in (d.get("productos") or []) if num(pr.get("cantidad")) != 0 and num(pr.get("precio")) > 0]
        tot_liq_imp = sum(num(pr.get("cantidad")) * num(pr.get("precio")) for pr in lineas_val) or None
        for pr in d.get("productos") or []:
            cult = cultivo_de(pr.get("producto"))
            if not cult: continue
            cant = num(pr.get("cantidad")); precio = num(pr.get("precio"))
            if cant == 0 or precio <= 0: continue
            fij = str(pr.get("fijacion") or "").split(",")
            comp = fij[0].strip().upper().replace(" ", "") if len(fij) >= 3 else ""
            fecha_fij = fij[1].strip() if len(fij) >= 3 else ""
            doc = str(pr.get("traslado") or "").strip()
            m = mapa(doc, "compra") if doc else {"cto": "", "ctg": "", "org": "", "dest": ""}
            cto_fij = comp2nombre.get(comp, "")
            if p2526(pr):
                cto = cto_fij or m["cto"]
                via = "fijacion" if cto_fij else ("traslado" if cto else "")
                if not cto:
                    cto, orgn = cto_por_prov(d.get("proveedor"), cult)
                    via = f"proveedor: {orgn[:25]}" if cto else f"SIN CONTRATO ({orgn[:25]})"
            else:
                if m["cto"] in HIS: cto, via = m["cto"], "traslado (partida mal etiquetada)"
                else: continue
            imp = cant * precio
            share = (imp / tot_liq_imp) if tot_liq_imp else 0
            tc = num(d.get("tc_dol")) or None
            compra_rows.append({"liq": nombre, "cto": cto, "via": via, "cultivo": cult,
                                "prov": prov_por_cto.get(cto, m.get("org", "")),
                                "fecha": d.get("fecha"), "ffij": fecha_fij or d.get("fecha"),
                                "ctg": m.get("ctg", ""), "doc": doc, "dest": m.get("dest", ""),
                                "tn": cant, "pc": precio, "tc": tc,
                                "pc_usd": round(precio / tc, 2) if tc else None,
                                "com": round(num(con.get("RECCOM")) * share, 2),
                                "sel": round(num(con.get("GTOSCOM")) * share, 2)})
print(f"compra: {len(compra_rows)} tramos · sin contrato: {len([t for t in compra_rows if t['via'].startswith('SIN')])}")

# ── VENTA: por camión con gastos reales
com_mayor = json.loads((DATA / "com_venta_por_liq.json").read_text(encoding="utf-8")) if (DATA / "com_venta_por_liq.json").exists() else {}
tarifas = json.loads((DATA / "tarifas_venta.json").read_text(encoding="utf-8")) if (DATA / "tarifas_venta.json").exists() else {}
def tarifa_de(org):
    cu = (org or "").strip().upper()
    for o, t in tarifas.items():
        if cu.startswith(o[:18]) or o.startswith(cu[:18]): return t
    return None
venta_rows = []
for f, pref in SERIES_V:
    p = DATA / f
    if not p.exists(): continue
    for n, d in json.loads(p.read_text(encoding="utf-8")).items():
        nombre = f"{pref} - {n}"
        prods = d.get("productos") or []
        granos = [pr for pr in prods if cultivo_de(pr.get("producto")) and p2526(pr) and num(pr.get("cantidad")) > 0]
        if not granos: continue
        def tiene_partida(pr): return str(pr.get("partida") or "").strip() not in ("", "None")
        granos_all = [pr for pr in prods if tiene_partida(pr) and num(pr.get("cantidad")) > 0]
        bruto_liq = sum(num(pr.get("cantidad")) * num(pr.get("precio")) for pr in granos_all)
        comi = sell = otros = 0.0
        for pr in prods:
            if tiene_partida(pr): continue
            nomp = str(pr.get("producto") or "").lower()
            impg = num(pr.get("cantidad")) * num(pr.get("precio"))
            if not impg: continue
            if "comercializa" in nomp or "honorarios" in nomp: comi += -impg
            elif "sellado" in nomp or "registro" in nomp: sell += -impg
            else: otros += -impg
        fuente = "liq"
        if comi == 0 and sell == 0:
            mj = com_mayor.get(nombre)
            if mj:
                comi, sell = num(mj.get("comision")), num(mj.get("sellado"))
                otros = -num(mj.get("bonif") or 0); fuente = "mayor"
        for pr in granos:
            cult = cultivo_de(pr.get("producto"))
            cant = num(pr.get("cantidad")); precio = num(pr.get("precio"))
            imp = cant * precio
            doc = str(pr.get("traslado") or "").strip()
            m = mapa(doc, "venta") if doc else {"cto": "", "ctg": "", "org": "", "dest": ""}
            if not m["cto"]:
                dig = _re.sub(r"\D", "", str(d.get("comprobante") or ""))
                da = DOCADIC.get(dig) if len(dig) >= 4 else None
                if da: m = {**m, "cto": da["cto"], "org": m["org"] or da["org"]}
            if not m["org"]: m["org"] = org_nombre(d.get("proveedor"))
            fij = str(pr.get("fijacion") or "").split(",")
            fecha_fij = fij[1].strip() if len(fij) >= 3 else ""
            tc = num(d.get("tc_dol")) or None
            share = (imp / bruto_liq) if bruto_liq else 0
            c_l, s_l, o_l = comi * share, sell * share, otros * share
            fu = fuente
            if fuente == "liq" and comi == 0 and sell == 0:
                t = tarifa_de(m["org"])
                if t:
                    c_l = imp * num(t.get("comision")) / 100; s_l = imp * num(t.get("sellado")) / 100
                    o_l = imp * num(t.get("otros")) / 100; fu = "tarifa"
                else: fu = "sin dato"
            venta_rows.append({"liq": nombre, "cultivo": cult, "fecha": d.get("fecha"), "ffij": fecha_fij,
                               "cto": m["cto"], "org": m["org"], "dest": m["dest"], "ctg": m["ctg"], "doc": doc,
                               "tn": cant, "pv": precio, "tc": tc,
                               "pv_usd": round(precio / tc, 2) if tc else None,
                               "com": round(c_l, 2), "sel": round(s_l, 2), "otros": round(o_l, 2), "fu": fu})
print(f"venta: {len(venta_rows)} camiones")
CACHE_ORG.write_text(json.dumps(org_cache, ensure_ascii=False), encoding="utf-8")

# ── convenios (compra liquidada sin liq de granos)
con_liq = {t["cto"] for t in compra_rows}
convenio = {}
for c, f2 in fnn.items():
    if c in con_liq or f2["liquidada"] <= 0: continue
    convenio[c] = {"tn": f2["liquidada"], "moneda": f2["moneda"], "precio": f2["precio_liq"],
                   "org": prov_por_cto.get(c, ""), "fecha": f2["fecha"],
                   "cultivo": cultivo_de(f2.get("producto")) or ""}

# ── merge con el archivo del tablero preservando los flags de canje
out_fp = DATA / "tablero_trigo_cruce.json"
canje = {}
if out_fp.exists():
    try: canje = (json.loads(out_fp.read_text(encoding="utf-8"))).get("canje") or {}
    except Exception: pass
out = {"compra": compra_rows, "venta": venta_rows, "convenio": convenio, "canje": canje}
out_fp.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
res = defaultdict(lambda: [0.0, 0.0])
for t in compra_rows: res[t["cultivo"]][0] += t["tn"]
for t in venta_rows: res[t["cultivo"]][1] += t["tn"]
print("== resumen por cultivo (tn compra / tn venta):")
for c, (tc_, tv) in sorted(res.items(), key=lambda x: -x[1][1]):
    print(f"   {c:10} {tc_:>10,.1f} / {tv:>10,.1f}")
print(f"convenios: {len(convenio)} · archivo: {out_fp}")
