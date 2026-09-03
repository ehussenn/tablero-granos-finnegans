# -*- coding: utf-8 -*-
"""Cruce ARCA (Cartas de Porte Electronicas / CTG) vs Finnegans.

Lado FINNEGANS (este modulo, funciona solo): baja /reports/trasladoGranos y arma
la tabla a nivel CTG con todo lo que se puede confrontar contra ARCA:
  CTG, carta de porte, fecha, grano, kg netos, kg confirmados AFIP, patente,
  chofer, transportista, titular, destinatario, origen/destino, cosecha,
  subtipo (compra / venta / traslado propio) y estado.

Lado ARCA: scripts/arca_cpe_scraper.py deja el export en data/arca/cpe_*.json;
cruzar() empareja por numero de CTG y marca las diferencias.

Uso:
    py scripts/arca_ctg_cruce.py                  # solo lado Finnegans -> data/ctg_finnegans.json
    py scripts/arca_ctg_cruce.py --cruce          # cruza con lo que haya de ARCA
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import finnegans_api as api

RAIZ = Path(__file__).resolve().parent.parent
SALIDA = RAIZ / "data" / "ctg_finnegans.json"
DESDE = "2025-07-01"          # campana 25/26 completa (el trigo entrega nov-dic 2025)
HASTA = "2030-12-31"

# subtipo -> flujo (como lo lee el usuario)
FLUJO = {
    "Recepción de Granos COMPRA CV":            "COMPRA",
    "Recepción de Semilla COMPRA":              "COMPRA SEMILLA",
    "Recepción de Semilla TERCEROS":            "COMPRA SEMILLA",
    "Remito compra granos - Compensación Convenio": "COMPENSACIÓN",
    "Traslado de Granos VENTA CV":              "VENTA",
    "Remito venta granos - Compensación Convenio": "COMPENSACIÓN",
    "Remito venta ALQUILERES":                  "ALQUILER",
    "Traslado CPE Agronasaja":                  "PRODUCCIÓN (CPE propia)",
    "Recepción de Semilla PROPIA":              "SEMILLA PROPIA",
}


def _num(x) -> float:
    try:
        return float(x or 0)
    except Exception:
        return 0.0


def normaliza_ctg(x) -> str:
    """Solo digitos: ARCA y Finnegans escriben el CTG con o sin guiones/espacios."""
    return re.sub(r"\D", "", str(x or ""))


def fetch_finnegans(desde: str = DESDE, hasta: str = HASTA) -> list[dict]:
    rows = api.call("/reports/trasladoGranos",
                    {"PARAMFechaDesde": desde, "PARAMFechaHasta": hasta})
    if not isinstance(rows, list):
        raise RuntimeError(f"trasladoGranos no devolvio filas: {rows!r}")
    out = []
    for r in rows:
        ctg = normaliza_ctg(r.get("NUMERODOCUMENTOADICIONAL"))
        sub = r.get("TRANSACCIONSUBTIPONOMBRE") or ""
        out.append({
            "ctg":          ctg,
            "cp":           r.get("NUMERODOCUMENTO") or "",
            "fecha":        r.get("FECHA") or "",
            "fecha_partida": r.get("FECHAPARTIDA") or "",
            "fecha_descarga": r.get("FECHADESCARGA") or "",
            "grano":        r.get("GRANO") or "",
            "kg":           _num(r.get("PESONETO")),
            "kg_sin_merma": _num(r.get("PESONETOSINMERMAS")),
            "kg_afip":      _num(r.get("KILOSCONFIRMADOSAFIP")),
            "patente":      (r.get("PATENTECAMION") or "").strip().upper(),
            "chofer":       r.get("CHOFER") or "",
            "transportista": r.get("TRANSPORTISTA") or "",
            "titular":      r.get("TITULAR") or "",
            "destinatario": r.get("DESTINATARIO") or "",
            "destino":      r.get("DESTINO") or "",
            "org":          r.get("ORGANIZACIONNOMBRE") or "",
            "loc_origen":   r.get("LOCALIDADORIGEN") or "",
            "loc_destino":  r.get("LOCALIDADDESTINO") or "",
            "prov_origen":  r.get("PROVINCIAORIGEN") or "",
            "prov_destino": r.get("PROVINCIADESTINO") or "",
            "cosecha":      r.get("COSECHA") or "",
            "subtipo":      sub,
            "flujo":        FLUJO.get(sub, sub or "—"),
            "estado":       r.get("ESTADO") or "",
            "estado_ctg":   r.get("ESTADO CTG") or "",
            "cancelado":    bool(r.get("CODIGOCANCELACIONCTG")),
            "contrato":     r.get("NUMERODOCUMENTOCONTRATO") or "",
            "doc":          r.get("DOCUMENTO") or "",
        })
    return out


def resumen(rows: list[dict]) -> dict:
    con = [r for r in rows if r["ctg"]]
    sin = [r for r in rows if not r["ctg"]]
    porflujo, dup = {}, {}
    for r in con:
        d = porflujo.setdefault(r["flujo"], {"n": 0, "tn": 0.0})
        d["n"] += 1
        d["tn"] += r["kg"] / 1000
        dup[r["ctg"]] = dup.get(r["ctg"], 0) + 1
    return {
        "filas": len(rows), "con_ctg": len(con), "sin_ctg": len(sin),
        "ctg_unicos": len(dup),
        "ctg_repetidos": {k: v for k, v in dup.items() if v > 1},
        "por_flujo": porflujo,
        "tn_total": sum(r["kg"] for r in con) / 1000,
    }


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    rows = fetch_finnegans()
    res = resumen(rows)
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    SALIDA.write_text(json.dumps({"desde": DESDE, "hasta": HASTA,
                                  "resumen": res, "rows": rows},
                                 ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[+] Finnegans: {res['filas']} traslados · {res['con_ctg']} con CTG "
          f"({res['ctg_unicos']} CTG unicos) · {res['sin_ctg']} sin CTG")
    print(f"    {res['tn_total']:,.1f} tn con CTG")
    for f, d in sorted(res["por_flujo"].items(), key=lambda x: -x[1]["tn"]):
        print(f"      {d['n']:6d} camiones · {d['tn']:11,.1f} tn  {f}")
    if res["ctg_repetidos"]:
        print(f"    [!] {len(res['ctg_repetidos'])} CTG aparecen en mas de un traslado "
              f"(normal en compra+venta del mismo camion)")
    print(f"[+] Escrito {SALIDA}")


# =============================================================================
#  CRUCE ARCA vs FINNEGANS
#  Regla validada contra el Excel del usuario (03/09/2026): un CTG de ARCA
#  "falta ingresar" si no aparece en NINGUNA punta de Finnegans (ni venta ni
#  compra). Cruzando por punta daba falsos positivos: hay CPE donde Agronasaja
#  participa que estan cargadas del lado venta (y viceversa).
#  Reprodujo exacto sus corridas: 26 y 5 en abril, y 8 de compra en la ultima.
#  Salida con la misma estructura que su Power BI "Cruce Cp":
#  CartaPorte, CTG, Fecha, Cultivo, Kg, Estado, Origen, Existe_en_Finnegans.
# =============================================================================
ARCA_DIR = RAIZ / "data" / "arca"

# estados de ARCA que SI hay que ingresar (el resto no genera movimiento real)
ESTADOS_A_INGRESAR = {"Confirmada", "Activa", "Activa con confirmacion de arribo",
                      "Activa con contingencia", "Descargado en destino"}


def _f(x) -> float:
    try:
        return float(str(x).replace(".", "").replace(",", "."))
    except Exception:
        return 0.0


def _fecha(s: str):
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(s).strip(), fmt).date()
        except Exception:
            pass
    return None


def cargar_arca() -> list:
    """Lee lo bajado por scripts/arca_cpe_scraper.py (solicitadas + participantes)."""
    filas = []
    for nombre, origen in (("cpe_solicitadas.json", "Solicitada"),
                           ("cpe_participantes.json", "Participante")):
        f = ARCA_DIR / nombre
        if not f.exists():
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        for r in d.get("filas", []):
            ctg = normaliza_ctg(r.get("CTG/CTDG"))
            if not ctg:
                continue
            det = r.get("_criterio") or r.get("_rol") or ""
            filas.append({
                "ctg": ctg,
                "cp": (r.get("Nro Carta Porte") or "").strip(),
                "fecha": (r.get("Fecha de emision") or r.get("Fecha de emisi\u00f3n") or "").strip(),
                "cultivo": (r.get("Tipo Grano") or "").strip(),
                "kg": _f(r.get("Kilos")),
                "estado": (r.get("Estado") or "").strip(),
                "origen": origen + (" - " + det if det else ""),
                "origen_corto": origen,
                "cuit_solicitante": (r.get("Cuit Solicitante") or "").strip(),
                "patente": (r.get("N. Patente") or "").strip(),
                "cuit_destino": (r.get("Cuit Destino") or "").strip(),
                "cuit_destinatario": (r.get("CUIT Destinatario") or "").strip(),
            })
    return filas


def cruzar(arca=None, fnn=None) -> dict:
    """Cruza por CTG. Devuelve el payload que consume la solapa del tablero."""
    arca = arca if arca is not None else cargar_arca()
    if fnn is None:
        fnn = (json.loads(SALIDA.read_text(encoding="utf-8"))["rows"]
               if SALIDA.exists() else fetch_finnegans())

    # indice Finnegans por CTG (un CTG puede estar en compra y venta a la vez)
    porctg = {}
    for r in fnn:
        if r["ctg"]:
            porctg.setdefault(r["ctg"], []).append(r)

    # ARCA deduplicado por CTG (el mismo camion aparece en varios roles)
    uniq = {}
    for r in arca:
        e = uniq.get(r["ctg"])
        if not e:
            uniq[r["ctg"]] = dict(r, origenes=[r["origen"]])
        elif r["origen"] not in e["origenes"]:
            e["origenes"].append(r["origen"])

    filas, faltan = [], []
    for ctg, r in uniq.items():
        en_fnn = porctg.get(ctg) or []
        fila = {
            "CartaPorte": r["cp"], "CTG": ctg, "Fecha": r["fecha"], "Cultivo": r["cultivo"],
            "Kg": r["kg"], "Estado": r["estado"],
            "Origen": " + ".join(sorted({o.split(" - ")[0] for o in r["origenes"]})),
            "OrigenDetalle": "; ".join(sorted(set(r["origenes"]))),
            "Existe_en_Finnegans": "Si" if en_fnn else "No",
            "flujo_fnn": " + ".join(sorted({x["flujo"] for x in en_fnn})) if en_fnn else "",
            "kg_fnn": round(sum(x["kg"] for x in en_fnn), 1) if en_fnn else 0.0,
            "patente": r["patente"], "cuit_solicitante": r["cuit_solicitante"],
            "a_ingresar": (not en_fnn) and r["estado"] in ESTADOS_A_INGRESAR,
        }
        if en_fnn and r["kg"] > 0:
            kg_v = max((x["kg"] for x in en_fnn), default=0.0)
            fila["dif_kg"] = round(kg_v - r["kg"], 1)
        else:
            fila["dif_kg"] = 0.0
        filas.append(fila)
        if not en_fnn:
            faltan.append(fila)

    # al reves: cargado en Finnegans y sin CPE en ARCA (solo dentro del rango bajado)
    rango = [d for d in (_fecha(r["fecha"]) for r in arca) if d]
    d_min, d_max = (min(rango), max(rango)) if rango else (None, None)
    sin_arca = []
    for ctg, rs in porctg.items():
        if ctg in uniq:
            continue
        r0 = rs[0]
        fd = _fecha(r0["fecha"])
        if d_min and fd and not (d_min <= fd <= d_max):
            continue
        if d_min and not fd:
            continue
        sin_arca.append({"CTG": ctg, "CartaPorte": r0["cp"], "Fecha": r0["fecha"],
                         "Cultivo": r0["grano"], "Kg": r0["kg"], "Flujo": r0["flujo"],
                         "Organizacion": r0["org"], "Estado": r0["estado_ctg"] or r0["estado"]})

    def agrupa(rows, campo):
        m = {}
        for r in rows:
            k = r.get(campo) or "-"
            d = m.setdefault(k, {"camiones": 0, "tn": 0.0})
            d["camiones"] += 1
            d["tn"] += r.get("Kg", 0) / 1000
        return dict(sorted(m.items(), key=lambda x: -x[1]["camiones"]))

    a_ing = [r for r in faltan if r["a_ingresar"]]
    return {
        "generado": datetime.now().isoformat(timespec="seconds"),
        "rango_arca": [d_min.isoformat() if d_min else None, d_max.isoformat() if d_max else None],
        "kpi": {
            "arca_ctg": len(uniq), "fnn_ctg": len(porctg),
            "no_ingresados": len(faltan),
            "no_ingresados_tn": round(sum(r["Kg"] for r in faltan) / 1000, 1),
            "a_ingresar": len(a_ing),
            "a_ingresar_tn": round(sum(r["Kg"] for r in a_ing) / 1000, 1),
            "sin_arca": len(sin_arca),
            "dif_kg_n": sum(1 for r in filas if abs(r["dif_kg"]) > 50),
        },
        "por_cultivo": agrupa(faltan, "Cultivo"),
        "por_origen": agrupa(faltan, "Origen"),
        "por_estado": agrupa(faltan, "Estado"),
        "faltan": sorted(faltan, key=lambda r: (r["Estado"] != "Confirmada", r["Fecha"])),
        "sin_arca": sorted(sin_arca, key=lambda r: r["Fecha"]),
        "difs": sorted([r for r in filas if abs(r["dif_kg"]) > 50],
                       key=lambda r: -abs(r["dif_kg"]))[:400],
        "total_filas": len(filas),
    }


def main_cruce() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    res = cruzar()
    k = res["kpi"]
    print(f"[+] ARCA {k['arca_ctg']} CTG - Finnegans {k['fnn_ctg']} CTG "
          f"(rango ARCA {res['rango_arca'][0]} a {res['rango_arca'][1]})")
    print(f"    NO ingresados en Finnegans: {k['no_ingresados']} camiones - {k['no_ingresados_tn']:,.1f} tn")
    print(f"    de esos, A INGRESAR (estado activo/confirmado): {k['a_ingresar']} - {k['a_ingresar_tn']:,.1f} tn")
    print(f"    en Finnegans sin CPE en ARCA: {k['sin_arca']}")
    print(f"    con diferencia de kilos > 50: {k['dif_kg_n']}")
    print("\n    por estado:")
    for e, d in res["por_estado"].items():
        print(f"      {d['camiones']:5d} camiones - {d['tn']:9,.1f} tn  {e}")
    print("\n    por cultivo:")
    for c, d in list(res["por_cultivo"].items())[:8]:
        print(f"      {d['camiones']:5d} camiones - {d['tn']:9,.1f} tn  {c}")
    f = RAIZ / "data" / "arca_cruce.json"
    f.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n[+] Escrito {f}")


if __name__ == "__main__":
    if "--cruce" in sys.argv:
        main_cruce()
    else:
        main()
