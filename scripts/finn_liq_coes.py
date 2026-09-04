# -*- coding: utf-8 -*-
"""Lado FINNEGANS del cruce de liquidaciones: junta todos los COE que ya estan
cargados en Finnegans, para poder decir cual falta ingresar.

Dos fuentes, porque ninguna sola alcanza:

  1) API de transacciones (/api/liquidacionCompraGranos/<DOC - N>): las 7 series de
     liquidaciones que usa Agronasaja. El COE es el campo NumeroComprobante.
        compra: LIQPRICPRA · LIQCPRAGRA · LIQCPRASEM
        venta : LIQ-PRI-VTA · LIQ-SEC-VTA · LIQ-VTA-INT · LIQ-PRI-VTA-INT
     Se escanea de a un numero interno; los snapshots de data/liq_*_api.json se
     reusan y se sigue desde el ultimo N (incremental, unas pocas decenas de
     llamadas por corrida).

  2) Datawarehouse Postgres (agronasajasrl_liquidacion_venta_granos): las
     liquidaciones de VENTA con su organizacion, grano y estado. Ahi el COE vive en
     numerodocumento (numerocoe viene casi siempre vacio). Cubre desde 03/2025 y es
     la fuente mas fresca del lado venta.

Salida: data/liq_coes_finnegans.json

Uso:
    py scripts/finn_liq_coes.py              # incremental (recomendado)
    py scripts/finn_liq_coes.py --sin-api    # solo DW, sin tocar la API
    py scripts/finn_liq_coes.py --desde-cero # rescanea las series enteras
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
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

RAIZ = Path(__file__).resolve().parent.parent
DATA = RAIZ / "data"
sys.path.insert(0, str(RAIZ / "scripts"))
from _env import need                       # carga el .env
import finnegans_api as api

# archivo snapshot, prefijo del documento, lado
SERIES = [
    ("liq_compra_api.json",      "LIQPRICPRA",      "compra"),
    ("liq_cpragra_api.json",     "LIQCPRAGRA",      "compra"),
    ("liq_cprasem_api.json",     "LIQCPRASEM",      "compra"),
    ("liq_venta_pri_api.json",   "LIQ-PRI-VTA",     "venta"),
    ("liq_venta_sec_api.json",   "LIQ-SEC-VTA",     "venta"),
    ("liq_vta_int_api.json",     "LIQ-VTA-INT",     "venta"),
    ("liq_pri_vta_int_api.json", "LIQ-PRI-VTA-INT", "venta"),
]

_tok = {"v": None}


def log(*a):
    print(*a, flush=True)


def get_doc(codigo: str, reintentos: int = 3):
    """GET de una liquidacion por su documento. None = no existe (404)."""
    if not _tok["v"]:
        _tok["v"] = api.get_token()
    ep = "https://api.finneg.com/api/liquidacionCompraGranos/" + urllib.parse.quote(codigo)
    for i in range(reintentos):
        try:
            req = urllib.request.Request(ep, headers={"Authorization": f"Bearer {_tok['v']}"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8", "ignore"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (401, 403):
                _tok["v"] = api.get_token(force_refresh=True)
            time.sleep(1.5)
        except Exception:
            time.sleep(2)
    return "ERROR"


def resumen(d: dict) -> dict:
    """Deja de una liquidacion solo lo que hace falta para el cruce."""
    def f(v):
        try:
            return float(str(v).replace(",", "") or 0)
        except Exception:
            return 0.0
    prods = [{"producto": p.get("ProductoCodigo"), "cantidad": p.get("Cantidad"),
              "precio": p.get("Precio")} for p in (d.get("Productos") or [])]
    # las toneladas de grano: los conceptos de gasto vienen con cantidad 1
    kg = sum(f(p.get("cantidad")) for p in prods
             if f(p.get("cantidad")) > 1.5 and "sellado" not in str(p.get("producto") or "").lower())
    return {"fecha": (d.get("Fecha") or "")[:10],
            "proveedor": d.get("Proveedor"),
            "comprobante": str(d.get("NumeroComprobante") or "").strip(),
            "tn": round(kg, 3),
            "productos": prods}


def actualiza_serie(archivo: str, pref: str, desde_cero: bool, tope_vacios: int = 60) -> dict:
    """Lee el snapshot y sigue escaneando desde el ultimo numero interno."""
    p = DATA / archivo
    d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    if desde_cero:
        d = {}
    ns = [int(k) for k in d if str(k).isdigit()]
    n = 0 if desde_cero else (max(ns) if ns else 0)
    log(f"    {pref:16s} snapshot {len(d):5d} docs · sigo desde {n + 1}")
    nuevos, vacios = 0, 0
    while vacios < tope_vacios:
        n += 1
        r = get_doc(f"{pref} - {n}")
        if r is None:
            vacios += 1
            continue
        if r == "ERROR":
            log(f"      [!] error en {pref} - {n}, sigo")
            continue
        vacios = 0
        d[str(n)] = resumen(r)
        nuevos += 1
        if nuevos % 25 == 0:
            log(f"      +{nuevos} nuevas (ultima {pref} - {n})")
    if nuevos:
        p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    log(f"    {pref:16s} -> {len(d)} docs (+{nuevos} nuevas)")
    return d


def dw_venta() -> list[dict]:
    """Liquidaciones de venta del DW: ahi el COE esta en numerodocumento."""
    try:
        import psycopg2
        import psycopg2.extras
    except Exception as e:
        log(f"    [!] sin psycopg2: {e}")
        return []
    try:
        cn = psycopg2.connect(host=need("FNN_DW_HOST"), dbname="finnegansbi",
                              user=need("FNN_DW_USER"), password=need("FNN_DW_PASS"),
                              port=5432, sslmode="require", connect_timeout=25)
        cr = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cr.execute("""SELECT transaccionsubtiponombre, fecha, documento, numerodocumento,
                             organizacionnombre, estado, tipoliquidacion, grano, corredor,
                             numerocoe, descripcion
                      FROM public.agronasajasrl_liquidacion_venta_granos""")
        rows = [dict(r) for r in cr.fetchall()]
        cn.close()
        log(f"    DW venta -> {len(rows)} filas")
        return rows
    except Exception as e:
        log(f"    [!] DW venta: {e}")
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sin-api", action="store_true")
    ap.add_argument("--desde-cero", action="store_true")
    a = ap.parse_args()

    coes: dict[str, dict] = {}

    def poner(coe: str, r: dict):
        coe = "".join(ch for ch in str(coe or "") if ch.isdigit())
        if len(coe) != 12 or coe == "000000000000":
            return
        ant = coes.get(coe)
        if ant is None:
            coes[coe] = r
        else:
            # si ya estaba, completo lo que falte (el DW trae organizacion y grano)
            for k, v in r.items():
                if v and not ant.get(k):
                    ant[k] = v
            ant.setdefault("docs", [])
            if r.get("documento") and r["documento"] not in ant.get("docs", []):
                ant["docs"].append(r["documento"])

    if not a.sin_api:
        log("[1] API de transacciones (7 series, incremental)")
        for archivo, pref, lado in SERIES:
            d = actualiza_serie(archivo, pref, a.desde_cero)
            for n, r in d.items():
                poner(r.get("comprobante"), {
                    "lado": lado, "documento": f"{pref} - {n}", "fecha": r.get("fecha") or "",
                    "tn": r.get("tn") or 0, "fuente": "api",
                })

    log("\n[2] Datawarehouse (liquidaciones de venta)")
    for r in dw_venta():
        poner(r.get("numerodocumento"), {
            "lado": "venta",
            "documento": r.get("documento") or "",
            "fecha": (r.get("fecha") or "")[:10],
            "organizacion": r.get("organizacionnombre") or "",
            "grano": r.get("grano") or "",
            "estado": r.get("estado") or "",
            "subtipo": r.get("transaccionsubtiponombre") or "",
            "tipoliq": r.get("tipoliquidacion") or "",
            "fuente": "dw",
        })
        # el numerocoe, cuando viene cargado, es otro COE valido del mismo documento
        if r.get("numerocoe"):
            poner(r["numerocoe"], {
                "lado": "venta", "documento": r.get("documento") or "",
                "fecha": (r.get("fecha") or "")[:10],
                "organizacion": r.get("organizacionnombre") or "", "fuente": "dw-coe",
            })

    f = DATA / "liq_coes_finnegans.json"
    fechas = sorted(v.get("fecha") or "" for v in coes.values() if v.get("fecha"))
    f.write_text(json.dumps({
        "generado": datetime.now().isoformat(timespec="seconds"),
        "rango": [fechas[0] if fechas else "", fechas[-1] if fechas else ""],
        "por_lado": {l: sum(1 for v in coes.values() if v.get("lado") == l)
                     for l in ("compra", "venta")},
        "coes": coes,
    }, ensure_ascii=False), encoding="utf-8")
    log(f"\n[OK] {len(coes)} COE de Finnegans -> {f.name}")
    log(f"     rango {fechas[0] if fechas else '?'} a {fechas[-1] if fechas else '?'}")
    for l in ("compra", "venta"):
        log(f"     {l}: {sum(1 for v in coes.values() if v.get('lado') == l)}")


if __name__ == "__main__":
    main()
