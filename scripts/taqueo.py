"""Taqueo/Seguimiento fino de CTG para el tablero. Consolida:
  - seguimiento por grano (Soja/Maíz/Trigo Pan) × flujo (propio / consignación compra-venta)
  - cruce compra↔venta (descalces) y duplicados
  - falta vincular vs extranets scrapeados (Cargill/LDC/Bunge/Intagro)
  - pendiente de liquidar (de lo entregado) por cerealera, IDENTIFICANDO cada CTG
Se llama desde build.py: taqueo.compute(ventas_contratos, desde, hasta).
"""
import re, datetime, json
from collections import defaultdict
from pathlib import Path
try:
    import finnegans_api as api
except Exception:
    api = None
ROOT = Path(__file__).resolve().parent.parent

def _norm(c): c = re.sub(r"\D","",str(c or "")); return c.lstrip("0") or ""
def _pf(v):
    s=str(v or "").split("T")[0].split(" ")[0]
    for sep in ("-","/"):
        p=s.split(sep)
        if len(p)==3:
            try:
                if len(p[0])==4: return datetime.date(int(p[0]),int(p[1]),int(p[2]))
                return datetime.date(int(p[2]),int(p[1]),int(p[0]))
            except: return None
    return None
def _gr(g):
    g=str(g or "").lower()
    if "soja" in g and "sem" not in g: return "Soja"
    if ("maíz" in g or "maiz" in g) and not any(x in g for x in ("sem","pising","blanco","oleico")): return "Maíz"
    if "trigo" in g and "sem" not in g: return "Trigo Pan"
    return None
def _contnum(s):
    m = re.search(r"-\s*(\d+)", str(s or "")); return m.group(1) if m else None
def _cerealera(name):
    n=str(name or "").upper()
    for k,lbl in [("CARGILL","Cargill"),("DREYFUS","LDC"),("LDC","LDC"),("BUNGE","Bunge"),
                  ("ARGENTRADING","Intagro"),("INTAGRO","Intagro"),("COFCO","COFCO"),
                  ("COOPERATIVAS ARGENTINAS","ACA"),("FYO","FYO"),("ALLARIA","Allaria"),
                  ("VITERRA","Viterra"),("ADM","ADM"),("MOLINOS","Molinos"),
                  ("ACEITERA GENERAL","AGD"),("CHS","CHS")]:
        if k in n: return lbl
    return None

_FLUJO = {"Traslado CPE Agronasaja":"propio",
          "Recepción de Granos COMPRA CV":"compra",
          "Traslado de Granos VENTA CV":"venta"}
_EXTRANETS = {"Cargill":"data/cargill/quality.json","LDC":"data/ldc/quality.json",
              "Bunge":"data/bunge/quality.json","Intagro":"data/intagro/quality.json"}

def compute(ventas_contratos, desde="2026-01-01", hasta=None):
    hasta = hasta or datetime.date.today().isoformat()
    d0 = datetime.date.fromisoformat(desde); d1 = datetime.date.fromisoformat(hasta)
    res = {"ventana":[desde,hasta], "generado": datetime.datetime.now().isoformat(timespec="seconds")}
    if api is None:
        return {**res, "error":"sin api"}
    # traslados amplios (para linkear todos los CTG) + subset ventana
    allrows = api.call("/reports/trasladoGranos", {"PARAMFechaDesde":"2024-01-01","PARAMFechaHasta":"2030-12-31"})
    allrows = allrows if isinstance(allrows,list) else []
    win = [r for r in allrows if (lambda d: d and d0<=d<=d1)(_pf(r.get("FECHA")))]

    # ---- 1) seguimiento por grano × flujo (ventana) ----
    data = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for r in win:
        g=_gr(r.get("GRANO")); fl=_FLUJO.get(r.get("TRANSACCIONSUBTIPONOMBRE"))
        if not g or not fl: continue
        c=_norm(r.get("NUMERODOCUMENTOADICIONAL"))
        if c: data[g][fl][c].append(r)
    kg = lambda rs: round(sum(float(x.get("PESONETO") or 0) for x in rs)/1000.0,1)
    tn_all = lambda d: round(sum(kg(v) for v in d.values()),1)
    seg={}
    for g in ["Soja","Maíz","Trigo Pan"]:
        gd=data.get(g,{}); pr=gd.get("propio",{}); co=gd.get("compra",{}); ve=gd.get("venta",{})
        seg[g]={
            "propio":{"ctgs":len(pr),"tn":tn_all(pr),"duplicados":sorted(c for c,v in pr.items() if len(v)>1)},
            "compra":{"ctgs":len(co),"tn":tn_all(co),"duplicados":sorted(c for c,v in co.items() if len(v)>1)},
            "venta": {"ctgs":len(ve),"tn":tn_all(ve),"duplicados":sorted(c for c,v in ve.items() if len(v)>1)},
            "cierran":len(set(co)&set(ve)),
            "compra_sin_venta":sorted(set(co)-set(ve)),
            "venta_sin_compra":sorted(set(ve)-set(co)),
        }
    res["seguimiento"]=seg

    # ---- 2) taqueo BIDIRECCIONAL por cerealera (como el romaneo manual) ----
    # universo Finnegans venta (todas las fechas) para saber si un CTG está o no en Finnegans
    fnn_all=set(_norm(r.get("NUMERODOCUMENTOADICIONAL")) for r in allrows
               if r.get("OPERACIONTIPO")=="Venta" and _norm(r.get("NUMERODOCUMENTOADICIONAL")))
    # CTGs de Finnegans venta destinados a cada cerealera, EN LA VENTANA
    fnn_por_cer=defaultdict(set)
    for r in win:
        if r.get("OPERACIONTIPO")!="Venta": continue
        c=_norm(r.get("NUMERODOCUMENTOADICIONAL"))
        if not c: continue
        cer=_cerealera(r.get("DESTINATARIO")) or _cerealera(r.get("ORGANIZACIONNOMBRE"))
        if cer: fnn_por_cer[cer].add(c)

    fv={}
    for cer,path in _EXTRANETS.items():
        # fuente EXTRANET más completa disponible por cerealera
        ext_ctgs=set(); fuente="quality"; completo=False
        if cer=="Cargill":
            # Cargill: movements.json (Excel completo). CTG = parte tras el guión de legalDocument.
            fpm=ROOT/"data"/"cargill"/"movements.json"
            if fpm.exists():
                try: mov=json.loads(fpm.read_text(encoding="utf-8"))
                except: mov=[]
                for m in mov:
                    if not str(m.get("movementType","")).lower().startswith("recepcion"): continue  # solo lo recibido
                    ld=str(m.get("legalDocument") or "")
                    c=_norm(ld.split("-")[-1]) if "-" in ld else ""
                    if not c: continue
                    d=_pf(m.get("deliveryDate") or m.get("applicationDate"))
                    if d and not (d0<=d<=d1): continue
                    ext_ctgs.add(c)
                fuente="movements (Excel completo)"; completo=True
        if not ext_ctgs:
            fp=ROOT/path; ext={}
            if fp.exists():
                try: ext=json.loads(fp.read_text(encoding="utf-8"))
                except: ext={}
            tiene_f=any(isinstance(v,dict) and v.get("fecha") for v in ext.values())
            for k,v in ext.items():
                n=_norm(k)
                if not n: continue
                f=(v or {}).get("fecha") if isinstance(v,dict) else None
                d=_pf(f)
                if tiene_f and d and not (d0<=d<=d1): continue
                ext_ctgs.add(n)
            # OJO: quality/análisis NO es la descarga completa de entregas -> la dirección
            # Finnegans->cerealera no es confiable. Solo se marca completo la fuente movements (Cargill).
            completo=False
        fnn_cer=fnn_por_cer.get(cer,set())
        falta_finnegans=sorted(ext_ctgs - fnn_all)        # en extranet, no en Finnegans -> ingresar en Finnegans
        falta_extranet =sorted(fnn_cer - ext_ctgs) if completo else []  # en Finnegans, no en extranet -> ingresar en cerealera
        fv[cer]={
            "fuente":fuente, "completo":completo,
            "extranet":len(ext_ctgs), "finnegans_ventana":len(fnn_cer),
            "coinciden":len(ext_ctgs & fnn_all),
            "falta_en_finnegans":falta_finnegans,
            "falta_en_extranet":falta_extranet,
        }
    res["falta_vincular"]=fv

    # ---- 2b) CTG crudos con fecha (los dos lados) para cruce INTERACTIVO por rango en el panel ----
    raw={}
    for cer,path in _EXTRANETS.items():
        # lado Finnegans: venta destino cerealera, TODAS las fechas -> [ctg, fecha ISO]
        fnn_l=[]
        for r in allrows:
            if r.get("OPERACIONTIPO")!="Venta": continue
            c=_norm(r.get("NUMERODOCUMENTOADICIONAL"))
            if not c: continue
            if (_cerealera(r.get("DESTINATARIO")) or _cerealera(r.get("ORGANIZACIONNOMBRE")))==cer:
                d=_pf(r.get("FECHA")); fnn_l.append([c, d.isoformat() if d else None])
        # lado Extranet (completo donde se puede)
        ext_l=[]; fuente="quality"; completo=False
        if cer=="Cargill":
            fpm=ROOT/"data"/"cargill"/"movements.json"
            if fpm.exists():
                try: mov=json.loads(fpm.read_text(encoding="utf-8"))
                except: mov=[]
                for m in mov:
                    if not str(m.get("movementType","")).lower().startswith("recepcion"): continue
                    ld=str(m.get("legalDocument") or "")
                    c=_norm(ld.split("-")[-1]) if "-" in ld else ""
                    if not c: continue
                    d=_pf(m.get("deliveryDate") or m.get("applicationDate"))
                    ext_l.append([c, d.isoformat() if d else None])
                fuente="movements (descarga completa)"; completo=True
        else:
            fp=ROOT/path; ext={}
            if fp.exists():
                try: ext=json.loads(fp.read_text(encoding="utf-8"))
                except: ext={}
            for k,v in ext.items():
                c=_norm(k)
                if not c: continue
                f=(v or {}).get("fecha") if isinstance(v,dict) else None
                d=_pf(f); ext_l.append([c, d.isoformat() if d else None])
        raw[cer]={"fuente":fuente,"completo":completo,"finnegans":fnn_l,"extranet":ext_l}
    res["raw"]=raw

    # ---- 3) pendiente de liquidar (entregado) por cerealera, con CTGs ----
    pend={}
    for r in (ventas_contratos or []):
        tn=r.get("cantidadentregadapendienteliquidar") or 0
        if tn and tn>0.05:
            num=_contnum(r.get("contrato"))
            gm=re.search(r"\((Grano [^)]+)\)", str(r.get("contrato") or ""))
            if num: pend[num]={"contrato":r.get("contrato"),"grano":(gm.group(1) if gm else None),
                               "tn":round(tn,2),"num":num,"cerealera":None,"ctgs":[]}
    for r in allrows:
        if r.get("OPERACIONTIPO")!="Venta": continue
        c=_norm(r.get("NUMERODOCUMENTOADICIONAL"))
        if not c: continue
        num=_contnum(r.get("NOMBRECONTRATO")) or _contnum(r.get("NUMERODOCUMENTOCONTRATO"))
        if num in pend:
            e=pend[num]
            if c not in [x["ctg"] for x in e["ctgs"]]:
                e["ctgs"].append({"ctg":c,"fecha":r.get("FECHA"),"tn":round(float(r.get("PESONETO") or 0)/1000,2)})
            if not e["cerealera"]:
                e["cerealera"]=_cerealera(r.get("DESTINATARIO")) or _cerealera(r.get("ORGANIZACIONNOMBRE"))
    porcer=defaultdict(lambda:{"tn":0.0,"contratos":[]})
    for num,e in pend.items():
        cer=e["cerealera"] or "(sin asignar)"
        porcer[cer]["tn"]+=e["tn"]; porcer[cer]["contratos"].append(e)
    res["pendiente_liquidar"]={
        "total_tn":round(sum(v["tn"] for v in pend.values()),1),
        "por_cerealera":{k:{"tn":round(v["tn"],1),"n_contratos":len(v["contratos"]),
                            "contratos":sorted(v["contratos"],key=lambda x:-x["tn"])}
                         for k,v in sorted(porcer.items(),key=lambda kv:-kv[1]["tn"])}
    }
    return res
