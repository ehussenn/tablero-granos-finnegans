# -*- coding: utf-8 -*-
"""Rellena los snapshots viejos de liquidaciones con los datos que se estaban
tirando: el TRASLADO de cada renglon (vinculacionOrigen), el numero de contrato
del corredor, el % parcial y la moneda.

Por que hace falta: la API devuelve, renglon por renglon, que traslado esta
liquidando. Sin ese dato es imposible saber que carta de porte entro en que
liquidacion, y todos los CTG de un contrato de compra aparecian como "sin
liquidar" aunque estuvieran cobrados (caso CONT-CPRA-GRA - 1149, que tiene la
liquidacion LIQCPRAGRA - 1730 por 250 tn con sus 7 CTG adentro).

Solo vuelve a pedir las liquidaciones a las que les falta el dato; las que ya lo
tienen no se tocan. Se puede cortar y volver a correr: guarda cada 25.

Uso:
    py scripts/finn_liq_backfill.py               # todas las series
    py scripts/finn_liq_backfill.py LIQCPRAGRA    # una sola
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

RAIZ = Path(__file__).resolve().parent.parent
DATA = RAIZ / "data"
sys.path.insert(0, str(RAIZ / "scripts"))
from _env import need                      # noqa: F401  (carga el .env)
import finnegans_api as api

SERIES = [
    ("liq_compra_api.json",      "LIQPRICPRA"),
    ("liq_cpragra_api.json",     "LIQCPRAGRA"),
    ("liq_cprasem_api.json",     "LIQCPRASEM"),
    ("liq_venta_pri_api.json",   "LIQ-PRI-VTA"),
    ("liq_venta_sec_api.json",   "LIQ-SEC-VTA"),
    ("liq_vta_int_api.json",     "LIQ-VTA-INT"),
    ("liq_pri_vta_int_api.json", "LIQ-PRI-VTA-INT"),
]

_tok = {"v": None}


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


def enriquece(rec: dict, d: dict) -> dict:
    """Le agrega al registro guardado lo que faltaba, sin pisar lo que ya tenia."""
    api_prods = d.get("Productos") or []
    prods = rec.get("productos") or []
    # los renglones vienen en el mismo orden; si no coinciden en cantidad, se rehacen
    if len(prods) != len(api_prods):
        prods = [{"producto": p.get("ProductoCodigo"), "cantidad": p.get("Cantidad"),
                  "precio": p.get("Precio")} for p in api_prods]
    for p, a in zip(prods, api_prods):
        t = str(a.get("vinculacionOrigen") or "").strip()
        p["traslado"] = t or None
        if p.get("fijacion") in (None, ""):
            p["fijacion"] = a.get("vinculacionDestino")
        if p.get("partida") in (None, ""):
            p["partida"] = a.get("PartidaNumero")
    rec["productos"] = prods
    rec["contrato"] = str(d.get("NumeroContratoIntermediario") or "").strip() or None
    rec["parcial_pct"] = d.get("PorcentajeParcial")
    if not rec.get("moneda"):
        rec["moneda"] = d.get("MonedaCodigo")
    return rec


def falta(rec: dict) -> bool:
    """True si a esta liquidacion le falta el traslado en algun renglon de grano."""
    if rec.get("contrato") is None and "contrato" not in rec:
        return True
    for p in (rec.get("productos") or []):
        try:
            q = float(str(p.get("cantidad") or 0).replace(",", ""))
        except Exception:
            q = 0.0
        if q > 1.5 and "traslado" not in p:
            return True
    return False


def main() -> int:
    solo = sys.argv[1].upper() if len(sys.argv) > 1 else None
    tot_ok = tot_falta = 0
    for arch, pref in SERIES:
        if solo and pref.upper() != solo:
            continue
        p = DATA / arch
        if not p.exists():
            print(f"  {pref:16s} sin snapshot, salteo")
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        pend = sorted((k for k, v in d.items() if falta(v)), key=lambda k: int(k) if k.isdigit() else 0)
        print(f"  {pref:16s} {len(d):5d} liq · les falta el vinculo a {len(pend)}")
        tot_falta += len(pend)
        if not pend:
            continue
        hechas = errores = 0
        for i, k in enumerate(pend, 1):
            r = get_doc(f"{pref} - {k}")
            if r is None or r == "ERROR":
                errores += 1
            else:
                d[k] = enriquece(d[k], r)
                hechas += 1
            if i % 25 == 0 or i == len(pend):
                p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
                print(f"      {i}/{len(pend)} · con vinculo {hechas} · sin respuesta {errores}", flush=True)
        p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        tot_ok += hechas
        print(f"  {pref:16s} -> {hechas} completadas, {errores} sin respuesta")
    print(f"\n[+] LISTO · {tot_ok} liquidaciones completadas de {tot_falta} que faltaban")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
