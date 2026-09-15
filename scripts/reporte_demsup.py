# -*- coding: utf-8 -*-
"""Reporte de posiciones DEM-SUP (soja y trigo) + producción.  SOLO LECTURA.

Pedido del usuario (14/09/2026): "entrá al extranet de Agronasaja sin tocar nada,
solo de consulta, y cada una hora dame posiciones de DEM-SUP trigo y soja y
consultame lo de producción".

Qué consulta (todo GET, nada se escribe en ningún lado):
  1. La MISMA API que alimenta las vistas DEM-SUP del Extranet Agronasaja
     (/vistas/ops-demsup-soja y /vistas/ops-demsup-trigo). Hay que mandar el
     header Origin porque la API tiene lista blanca de origenes.
  2. El Portal de Producción de Agronasaja (página pública) para el avance de
     cosecha por cultivo.

Guarda cada corrida en data/reportes_demsup/ para poder comparar contra la
anterior y mostrar qué se movió en la última hora.

Uso:
    py scripts/reporte_demsup.py            # reporte + comparación con el anterior
    py scripts/reporte_demsup.py --json     # además deja el JSON crudo por pantalla
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

RAIZ = Path(__file__).resolve().parent.parent
OUT = RAIZ / "data" / "reportes_demsup"
API = "https://agnsja-operaciones-api.azurewebsites.net/api/operaciones/dem-sup-"
# La API valida el origen: es el mismo header que usa scripts/demsup_soja.py
HD = {"User-Agent": "tablero-granos (solo lectura)",
      "Origin": "https://sanguine86.github.io",
      "Referer": "https://sanguine86.github.io/"}
PORTAL = "https://sanguine86.github.io/agronasaja-produccion/index.html"
ART = timezone(timedelta(hours=-3))          # hora argentina

# columna -> (nombre, si suma al stock)
COLS = [("C", "Granel en Campo"), ("D", "Granel en Semillero"),
        ("K", "Stock Clasificado"), ("L", "Corte de Bolsa"),
        ("O", "Venta Pendiente"), ("P", "Venta Despachada"),
        ("S", "Prod Pendiente"), ("T", "Prod Despachado")]
BOLSA_KG = 40                                 # una bolsa = 40 kg


def n(v) -> int:
    try:
        return round(float(v or 0))
    except Exception:
        return 0


def miles(v) -> str:
    return f"{v:,}".replace(",", ".")


# ───────────────────────────── consultas ─────────────────────────────
def demsup(cult: str) -> dict:
    r = urllib.request.urlopen(urllib.request.Request(API + cult, headers=HD), timeout=120)
    return json.loads(r.read().decode("utf-8", "ignore"))


def produccion() -> dict:
    """Avance de cosecha del Portal de Producción (público, sin login)."""
    req = urllib.request.Request(PORTAL, headers={"User-Agent": "Mozilla/5.0"})
    h = urllib.request.urlopen(req, timeout=90).read().decode("utf-8", "replace")
    dec = json.JSONDecoder()
    lotes = None
    for m in re.finditer(r"\[\s*\{", h):
        try:
            a, _ = dec.raw_decode(h, m.start())
        except Exception:
            continue
        if (isinstance(a, list) and len(a) > 20 and isinstance(a[0], dict)
                and "haLote" in a[0] and "haCosechada" in a[0]):
            if lotes is None or len(a) > len(lotes):
                lotes = a
    if not lotes:
        return {}
    f = lambda x: float(x or 0)
    ag: dict[str, dict] = {}
    for l in lotes:
        c = " ".join(str(l.get("cultivo") or "").split()).upper()
        if not c:
            continue
        a = ag.setdefault(c, {"lotes": 0, "haL": 0.0, "haC": 0.0, "haP": 0.0,
                              "pend_ha": 0.0, "tn": 0.0, "terminados": 0})
        haL, hc, hp = f(l.get("haLote")), f(l.get("haCosechada")), f(l.get("haPerdidas"))
        a["lotes"] += 1
        a["haL"] += haL; a["haC"] += hc; a["haP"] += hp
        a["tn"] += f(l.get("tnAgnsj"))
        # el lote se da por terminado con avance = 1 (misma regla que el tablero)
        if f(l.get("avance")) >= 0.999:
            a["terminados"] += 1
        else:
            a["pend_ha"] += max(0.0, haL - hc - hp)
    return ag


# ───────────────────────────── armado ─────────────────────────────
def totales(d: dict) -> dict:
    t = {c: 0 for c, _ in COLS}
    for _, r in (d.get("rows") or {}).items():
        for c, _ in COLS:
            t[c] += n(r.get(c))
    return t


def snapshot() -> dict:
    s = {"consultado": datetime.now(ART).isoformat(timespec="seconds"), "cultivos": {}}
    for c in ("soja", "trigo"):
        try:
            d = demsup(c)
        except Exception as e:
            s["cultivos"][c] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
            continue
        s["cultivos"][c] = {
            "fecha_vista": d.get("fecha"),
            "generado": d.get("generatedAt"),
            "tot": totales(d),
            "rows": {v: {c2: n(r.get(c2)) for c2, _ in COLS}
                     for v, r in (d.get("rows") or {}).items()},
        }
    try:
        s["produccion"] = produccion()
    except Exception as e:
        s["produccion"] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    return s


def anterior() -> dict | None:
    OUT.mkdir(parents=True, exist_ok=True)
    fs = sorted(OUT.glob("demsup_*.json"))
    if not fs:
        return None
    try:
        return json.loads(fs[-1].read_text(encoding="utf-8"))
    except Exception:
        return None


def linea_dif(a: int, b: int) -> str:
    d = b - a
    if d == 0:
        return ""
    return f"   ({'+' if d > 0 else ''}{miles(d)})"


def informe(s: dict, ant: dict | None) -> str:
    L = []
    hh = s["consultado"][11:16]
    L.append(f"POSICIONES DEM-SUP · {s['consultado'][8:10]}/{s['consultado'][5:7]} {hh} hs (hora argentina)")
    if ant:
        L.append(f"comparado contra la consulta de las {ant['consultado'][11:16]} hs")
    L.append("")
    for cult in ("soja", "trigo"):
        d = s["cultivos"].get(cult) or {}
        if d.get("error"):
            L.append(f"── {cult.upper()} ── no pude consultarla: {d['error']}")
            L.append("")
            continue
        da = ((ant or {}).get("cultivos") or {}).get(cult) or {}
        ta, t = da.get("tot") or {}, d["tot"]
        L.append(f"══ SEMILLA {cult.upper()} ══ vista al {d.get('fecha_vista') or '—'}")
        L.append("")

        def bloque(titulo, cols, total=None, nota=None):
            L.append(f"   {titulo}")
            L.append(f"   {'CONCEPTO':24s} {'BOLSAS':>10s} {'TONELADAS':>11s}")
            for c, nom in cols:
                if t[c] == 0 and n(ta.get(c)) == 0:
                    continue
                L.append(f"   {nom:24s} {miles(t[c]):>10s} {t[c]*BOLSA_KG/1000:>11,.1f}"
                         + (linea_dif(n(ta.get(c)), t[c]) if ta else ""))
            if total:
                tot = sum(t[c] for c in total)
                L.append(f"   {'-'*47}")
                L.append(f"   {'TOTAL':24s} {miles(tot):>10s} {tot*BOLSA_KG/1000:>11,.1f}"
                         + (linea_dif(sum(n(ta.get(c)) for c in total), tot) if ta else ""))
            if nota:
                L.append(f"   {nota}")
            L.append("")

        # PLANTA: C + D + K. El corte de bolsa (L) es perdida del proceso: NO suma.
        bloque("PLANTA", [("C", "Granel en Campo"), ("D", "Granel en Semillero"),
                          ("K", "Stock Clasificado")], total=["C", "D", "K"])
        if t["L"] or n(ta.get("L")):
            L.append(f"   {'Corte de Bolsa':24s} {miles(t['L']):>10s} {t['L']*BOLSA_KG/1000:>11,.1f}"
                     + (linea_dif(n(ta.get('L')), t['L']) if ta else "")
                     + "   ← pérdida del proceso, no suma a Planta")
            L.append("")
        # PRODUCCION de SEMILLA (no es la produccion de cosecha: son cosas distintas)
        bloque("PRODUCCIÓN (semilla · pedidos de campo para siembra propia)",
               [("S", "Prod Pendiente"), ("T", "Prod Despachado")], total=["S", "T"])
        bloque("VENTA (semilla)",
               [("O", "Venta Pendiente"), ("P", "Venta Despachada")], total=["O", "P"],
               nota=f"Demanda Total Pendiente (venta pend. + prod pend.) = "
                    f"{miles(t['O'] + t['S'])} bls · {(t['O']+t['S'])*BOLSA_KG/1000:,.1f} tn")
        # que variedades se movieron
        if da.get("rows"):
            movs = []
            for v, r in d["rows"].items():
                ra = da["rows"].get(v) or {}
                for c, nom in COLS:
                    dd = r[c] - n(ra.get(c))
                    if dd:
                        movs.append((abs(dd), v, nom, dd))
            if movs:
                L.append("   se movió en la última hora:")
                for _, v, nom, dd in sorted(movs, reverse=True)[:10]:
                    L.append(f"     {v:14s} {nom:22s} {'+' if dd > 0 else ''}{miles(dd)} bls")
                L.append("")
    # producción
    pr = s.get("produccion") or {}
    if pr.get("error"):
        L.append(f"── PRODUCCIÓN ── no pude consultarla: {pr['error']}")
    elif pr:
        pa = (ant or {}).get("produccion") or {}
        L.append("══ COSECHA DE GRANOS ══ Portal de Producción")
        L.append("   (otra fuente y otra cosa: no confundir con la PRODUCCIÓN de semilla de arriba)")
        L.append(f"   {'CULTIVO':22s} {'LOTES':>6s} {'HA TOTAL':>10s} {'COSECHADA':>10s} "
                 f"{'AVANCE':>7s} {'HA PEND':>9s} {'TN AGNSJ':>10s}")
        for c, a in sorted(pr.items(), key=lambda kv: -kv[1]["haL"]):
            av = 100 * a["haC"] / a["haL"] if a["haL"] else 0
            dif = ""
            if pa.get(c):
                dd = round(a["haC"] - pa[c]["haC"], 1)
                if abs(dd) > 0.05:
                    dif = f"   ({'+' if dd > 0 else ''}{dd:,.1f} ha)"
            L.append(f"   {c[:22]:22s} {a['lotes']:>6} {a['haL']:>10,.1f} {a['haC']:>10,.1f} "
                     f"{av:>6.1f}% {a['pend_ha']:>9,.1f} {a['tn']:>10,.1f}{dif}")
    L.append("")
    L.append("Merma del granel (cols C y D, bolsas = kg × merma / 40): GLYCINE 1,0 · "
             "ARECO 0,4 · MORSE SILO 30 0,0 · resto (MORSE, PERGAMINO, SILOBOLSA) 0,9.")
    L.append("Consulta de solo lectura: no se modificó nada en el extranet ni en Finnegans.")
    return "\n".join(L)


def main() -> int:
    ant = anterior()
    s = snapshot()
    OUT.mkdir(parents=True, exist_ok=True)
    f = OUT / ("demsup_" + s["consultado"][:13].replace(":", "").replace("-", "").replace("T", "_") + ".json")
    f.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
    # no dejar que la carpeta crezca para siempre: se guardan las ultimas 200
    viejos = sorted(OUT.glob("demsup_*.json"))[:-200]
    for v in viejos:
        try:
            v.unlink()
        except Exception:
            pass
    print(informe(s, ant))
    if "--json" in sys.argv:
        print("\n" + json.dumps(s, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
