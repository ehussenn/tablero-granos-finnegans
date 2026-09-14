# -*- coding: utf-8 -*-
"""Trae las liquidaciones de UN contrato, sin correr de gusto.

Por que hace falta: la API no tiene un endpoint que devuelva las liquidaciones de
un contrato. Lo que si trae cada liquidacion es, renglon por renglon, el TRASLADO
que liquida (vinculacionOrigen). Y del datawarehouse sabemos que traslados tiene
cada carta de porte de un contrato. Cruzando los dos sale que CTG se liquido.

Como evita el trabajo al pedo:
  1) primero mira la BIBLIOTECA (data/liq_biblioteca.json): lo que ya se pidio
     alguna vez no se vuelve a pedir nunca mas
  2) despues mira los snapshots de siempre (data/liq_*_api.json)
  3) recien ahi, y solo si le faltan traslados, va a la API — de la liquidacion
     mas nueva hacia atras, y CORTA apenas ubico todos los traslados del contrato

Todo lo que baja queda guardado en la biblioteca, asi que la proxima vez que
pidas ese contrato (o cualquiera que comparta liquidacion) sale sin tocar la API.

Uso:
    py scripts/liq_contrato.py 1149            # contrato de compra 1149
    py scripts/liq_contrato.py 1149 --venta    # si es de venta
    py scripts/liq_contrato.py 1149 --max 40   # tope de llamadas a la API
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

RAIZ = Path(__file__).resolve().parent.parent
DATA = RAIZ / "data"
BIBLIO = DATA / "liq_biblioteca.json"
sys.path.insert(0, str(RAIZ / "scripts"))
from _env import need                       # noqa: F401  (carga el .env)
import finnegans_api as api

SERIES_COMPRA = [("liq_compra_api.json", "LIQPRICPRA"), ("liq_cpragra_api.json", "LIQCPRAGRA"),
                 ("liq_cprasem_api.json", "LIQCPRASEM")]
SERIES_VENTA = [("liq_venta_pri_api.json", "LIQ-PRI-VTA"), ("liq_venta_sec_api.json", "LIQ-SEC-VTA"),
                ("liq_vta_int_api.json", "LIQ-VTA-INT"), ("liq_pri_vta_int_api.json", "LIQ-PRI-VTA-INT")]

_tok = {"v": None}


# ───────────────────────────── biblioteca ─────────────────────────────
def leer_biblio() -> dict:
    if BIBLIO.exists():
        try:
            return json.loads(BIBLIO.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"    [!] biblioteca ilegible ({e}), arranco de cero")
    return {"generado": "", "liquidaciones": {}, "contratos": {}}


def guardar_biblio(b: dict) -> None:
    b["generado"] = datetime.now(timezone.utc).isoformat()
    BIBLIO.write_text(json.dumps(b, ensure_ascii=False, indent=1), encoding="utf-8")


# ───────────────────────────── API ─────────────────────────────
def get_doc(codigo: str, reintentos: int = 3):
    if not _tok["v"]:
        _tok["v"] = api.get_token()
    ep = "https://api.finneg.com/api/liquidacionCompraGranos/" + urllib.parse.quote(codigo)
    for i in range(reintentos):
        try:
            req = urllib.request.Request(ep, headers={"Authorization": f"Bearer {_tok['v']}"})
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.loads(r.read().decode("utf-8", "ignore"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (401, 403):
                _tok["v"] = api.get_token(force_refresh=True)
            time.sleep(2 * (i + 1))
        except Exception:
            time.sleep(3 * (i + 1))
    return "ERROR"


def resumir(codigo: str, d: dict) -> dict:
    """Lo que hace falta de una liquidacion, con el traslado de cada renglon."""
    prods = []
    for p in (d.get("Productos") or []):
        prods.append({"producto": p.get("ProductoCodigo"), "cantidad": p.get("Cantidad"),
                      "precio": p.get("Precio"),
                      "traslado": (str(p.get("vinculacionOrigen") or "").strip() or None),
                      "partida": p.get("PartidaNumero")})
    return {"liq": codigo,
            "fecha": (d.get("Fecha") or "")[:10],
            "comprobante": str(d.get("NumeroComprobante") or "").strip(),
            "contrato_corredor": str(d.get("NumeroContratoIntermediario") or "").strip() or None,
            "proveedor": d.get("Proveedor"),
            "moneda": d.get("MonedaCodigo"),
            "parcial_pct": d.get("PorcentajeParcial"),
            "tipo": d.get("TransaccionSubtipoCodigo"),
            "productos": prods}


# ───────────────────────────── datos del contrato ─────────────────────────────
def traslados_del_contrato(nro: str, lado: str) -> list[dict]:
    """Las cartas de porte del contrato y el traslado de cada una, del DW."""
    import psycopg2
    import psycopg2.extras
    cn = psycopg2.connect(host=need("FNN_DW_HOST"), dbname=os.environ.get("FNN_DW_DB", "finnegansbi"),
                          user=need("FNN_DW_USER"), password=need("FNN_DW_PASS"),
                          port=int(os.environ.get("FNN_DW_PORT", "5432")),
                          sslmode="require", connect_timeout=25)
    cr = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    pref = "CONT-CPRA-GRA" if lado == "compra" else "CTO-VTA-GRA"
    subs = (("Recepción de Granos COMPRA CV", "Recepción de Semilla TERCEROS",
             "Recepción de Semilla COMPRA", "Recepción de Semilla PROPIA",
             "Remito compra granos - Compensación Convenio") if lado == "compra"
            else ("Traslado CPE Agronasaja", "Traslado de Granos VENTA CV",
                  "Remito venta granos - Compensación Convenio"))
    cr.execute("""
        SELECT numerodocumentoadicional AS ctg, numerodocumento AS cp, documento,
               fecha, pesoneto, grano, organizacionnombre AS org, cosecha, destino
          FROM public.agronasajasrl_traslado_venta_granos_carta_porte_cruce
         WHERE nombrecontrato LIKE %s
           AND transaccionsubtiponombre = ANY(%s)
           AND COALESCE(numerodocumentoadicional,'') <> ''
         ORDER BY fecha, numerodocumento
    """, (f"%{pref} - {nro}%", list(subs)))
    filas = [dict(r) for r in cr.fetchall()]
    cn.close()
    return filas


def del_cache(series) -> dict:
    """traslado -> [liquidaciones] segun los snapshots de siempre."""
    m = {}
    for arch, pref in series:
        p = DATA / arch
        if not p.exists():
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for num, rec in d.items():
            for pr in (rec.get("productos") or []):
                t = str(pr.get("traslado") or "").strip()
                if t and t != "None":
                    m.setdefault(t, []).append({"liq": f"{pref} - {num}", "fecha": rec.get("fecha"),
                                                "tn": pr.get("cantidad")})
    return m


def sin_vinculo(series) -> list[str]:
    """Las liquidaciones cacheadas a las que les falta el traslado, de la mas
    nueva a la mas vieja (las que faltan son casi siempre las ultimas)."""
    faltan = []
    for arch, pref in series:
        p = DATA / arch
        if not p.exists():
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for num, rec in d.items():
            prods = rec.get("productos") or []
            grano = [x for x in prods if _f(x.get("cantidad")) > 1.5]
            if grano and any("traslado" not in x or not x.get("traslado") for x in grano):
                faltan.append((f"{pref} - {num}", rec.get("fecha") or "", int(num) if str(num).isdigit() else 0))
    faltan.sort(key=lambda t: (t[1], t[2]), reverse=True)
    return [t[0] for t in faltan]


def _f(v):
    try:
        return float(str(v).replace(",", "") or 0)
    except Exception:
        return 0.0


# ───────────────────────────── main ─────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("contrato")
    ap.add_argument("--venta", action="store_true", help="el contrato es de venta")
    ap.add_argument("--max", type=int, default=60, help="tope de liquidaciones a pedirle a la API")
    a = ap.parse_args()
    nro = "".join(c for c in a.contrato if c.isdigit())
    lado = "venta" if a.venta else "compra"
    series = SERIES_VENTA if a.venta else SERIES_COMPRA

    print(f"[+] Contrato de {lado} {nro}")
    cps = traslados_del_contrato(nro, lado)
    if not cps:
        print("    no tiene cartas de porte cargadas en el datawarehouse")
        return 1
    docs = {}
    for r in cps:
        docs.setdefault(str(r["documento"]).strip(), []).append(r)
    print(f"    {len(cps)} cartas de porte · {len(docs)} traslados")

    b = leer_biblio()
    # 1) biblioteca  2) snapshots
    enc = {}
    for t, ls in ((k, v) for k, v in b.get("por_traslado", {}).items()):
        if t in docs:
            enc[t] = ls
    cache = del_cache(series)
    for t in docs:
        if t not in enc and t in cache:
            enc[t] = cache[t]
    print(f"    ya sabia de {len(enc)} de {len(docs)} traslados (sin tocar la API)")

    # 3) recien ahora la API, y solo hasta ubicar lo que falta
    faltan = [t for t in docs if t not in enc]
    pedidas = 0
    if faltan:
        cand = sin_vinculo(series)
        print(f"    me faltan {len(faltan)} · reviso liquidaciones sin vinculo "
              f"({len(cand)}), de la mas nueva a la mas vieja, tope {a.max}")
        for cod in cand:
            if not faltan or pedidas >= a.max:
                break
            d = get_doc(cod)
            pedidas += 1
            if not isinstance(d, dict):
                continue
            rec = resumir(cod, d)
            b.setdefault("liquidaciones", {})[cod] = rec
            for pr in rec["productos"]:
                t = pr.get("traslado")
                if not t:
                    continue
                b.setdefault("por_traslado", {}).setdefault(t, [])
                if not any(x["liq"] == cod for x in b["por_traslado"][t]):
                    b["por_traslado"][t].append({"liq": cod, "fecha": rec["fecha"], "tn": pr.get("cantidad")})
                if t in faltan:
                    enc[t] = b["por_traslado"][t]
                    faltan.remove(t)
                    print(f"      ✓ {t} -> {cod}")
        print(f"    le pedi {pedidas} liquidaciones a la API")

    # resultado
    liqs, kg_liq, kg_tot = set(), 0.0, 0.0
    print(f"\n{'CTG':<14} {'CARTA DE PORTE':<18} {'FECHA':<11} {'KG':>10}  ESTADO")
    for r in cps:
        t = str(r["documento"]).strip()
        kg = _f(r["pesoneto"])
        kg_tot += kg
        ls = enc.get(t) or []
        if ls:
            kg_liq += kg
            for x in ls:
                liqs.add(x["liq"])
        est = ("LIQUIDADO en " + ", ".join(sorted({x["liq"] for x in ls}))) if ls else "SIN LIQUIDAR"
        print(f"{str(r['ctg']):<14} {str(r['cp'] or ''):<18} {str(r['fecha'])[:10]:<11} {kg:>10,.0f}  {est}")
    print(f"\n    liquidado {kg_liq:,.0f} kg · sin liquidar {kg_tot-kg_liq:,.0f} kg · total {kg_tot:,.0f} kg")
    if liqs:
        print(f"    liquidaciones: {', '.join(sorted(liqs))}")

    b.setdefault("contratos", {})[f"{lado}-{nro}"] = {
        "lado": lado, "numero": nro, "consultado": datetime.now(timezone.utc).isoformat(),
        "org": cps[0].get("org"), "grano": cps[0].get("grano"), "cosecha": cps[0].get("cosecha"),
        "kg_total": round(kg_tot, 2), "kg_liquidado": round(kg_liq, 2),
        "liquidaciones": sorted(liqs),
        "ctgs": [{"ctg": r["ctg"], "cp": r["cp"], "fecha": str(r["fecha"])[:10],
                  "kg": _f(r["pesoneto"]), "traslado": str(r["documento"]).strip(),
                  "liqs": sorted({x["liq"] for x in (enc.get(str(r["documento"]).strip()) or [])})}
                 for r in cps]}
    guardar_biblio(b)
    print(f"    guardado en la biblioteca ({len(b.get('liquidaciones', {}))} liquidaciones, "
          f"{len(b.get('contratos', {}))} contratos)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
