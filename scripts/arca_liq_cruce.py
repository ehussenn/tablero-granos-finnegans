# -*- coding: utf-8 -*-
"""CRUCE de LIQUIDACIONES: ARCA (LPG + LSG, emitidas y recibidas) vs Finnegans.

Entradas
    data/arca/lpg_liquidaciones.json   (scripts/arca_lpg_scraper.py)
    data/liq_coes_finnegans.json       (scripts/finn_liq_coes.py)

Regla del cruce (la misma que valido para las cartas de porte): la clave es el COE y
una liquidacion de ARCA figura como FALTA INGRESAR solo si su COE no aparece en
NINGUNA punta de Finnegans (ni compra ni venta). Cruzar punta contra punta daba
falsos positivos, porque una misma liquidacion puede estar cargada como venta
primaria, secundaria o de intermediario segun como se armo la operacion.

No se cuentan como pendientes las liquidaciones anuladas (estado "Anulado ..."):
esas no hay que cargarlas.

Salida: data/arca_liq_cruce.json  (lo consume la solapa "Cruce Liquidaciones" del tablero)

Uso:
    py scripts/arca_liq_cruce.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

RAIZ = Path(__file__).resolve().parent.parent
DATA = RAIZ / "data"

# Las cuatro solapas que pidio, en orden, con su etiqueta y de que lado esta
SOLAPAS = [
    ("lpg_recibidas",     "LPG Recibidas",     "venta",
     "Liquidaciones primarias que le emitieron a Agronasaja por sus VENTAS. "
     "Son las que hay que pasar a mano a Finnegans."),
    ("lsg_emitidas",      "LSG Emitidas",      "venta",
     "Liquidaciones secundarias que Agronasaja emite como vendedor."),
    ("lpg_emitidas",      "LPG Emitidas",      "compra",
     "Liquidaciones primarias que Agronasaja emite a los productores por sus COMPRAS."),
    ("lsg_recibidas",     "LSG Recibidas",     "compra",
     "Liquidaciones secundarias que le emitieron a Agronasaja (compras a no productores). "
     "ATENCION: esta solapa cruza muy poco (son pocas y casi ninguna aparece en Finnegans) "
     "— hay que ver con Javi si estas se cargan como liquidacion o de otra forma."),
    ("lsg_por_vendedor",  "LSG por Vendedor",  "venta",
     "Todas las secundarias donde Agronasaja es el vendedor, incluidas las que emitio "
     "un corredor por su cuenta."),
    ("lpg_por_comprador", "LPG por Comprador", "compra",
     "Todas las primarias donde Agronasaja es el comprador."),
]


def log(*a):
    print(*a, flush=True)


def anulada(estado: str) -> bool:
    return "anulad" in (estado or "").lower()


def _contraparte(p: dict) -> str:
    """Razon social de la otra parte, sacada del PDF: la que NO es Agronasaja.
    Las consultas de emitidas no traen la denominacion en la grilla."""
    for k in ("comprador", "vendedor"):
        v = (p.get(k) or "").strip()
        if v and "AGRONASAJA" not in v.upper():
            return v
    return ""


def main():
    fa = DATA / "arca" / "lpg_liquidaciones.json"
    ff = DATA / "liq_coes_finnegans.json"
    if not fa.exists():
        raise SystemExit(f"[!] falta {fa} — corre primero scripts/arca_lpg_scraper.py")
    if not ff.exists():
        raise SystemExit(f"[!] falta {ff} — corre primero scripts/finn_liq_coes.py")

    arca = json.loads(fa.read_text(encoding="utf-8"))
    fnn = json.loads(ff.read_text(encoding="utf-8"))
    FC: dict = fnn.get("coes") or {}
    log(f"[+] ARCA: {len(arca.get('filas') or [])} filas · Finnegans: {len(FC)} COE")

    # kilos e importes de cada liquidacion, sacados del PDF de ARCA
    # (scripts/arca_liq_kg.py). La grilla no los muestra.
    fd = DATA / "arca" / "lpg_detalle.json"
    DET: dict = {}
    if fd.exists():
        DET = (json.loads(fd.read_text(encoding="utf-8")).get("detalle") or {})
        log(f"    kilos del PDF de ARCA: {len(DET)} liquidaciones")
    else:
        log("    (sin kilos todavia: corre scripts/arca_liq_kg.py)")

    # nombre por CUIT: ARCA lo trae en algunas consultas y sirve para las otras
    nombre_cuit: dict[str, str] = {}
    for r in arca["filas"]:
        c, d = r.get("cuit") or "", (r.get("denominacion") or "").strip()
        if c and d and d.upper() != "AGRONASAJA SRL":
            nombre_cuit.setdefault(c, d)

    filas = []
    for r in arca["filas"]:
        coe = r["coe"]
        f = FC.get(coe)
        p = DET.get(coe) or {}
        # En las LSG recibidas la grilla pone AGRONASAJA SRL en "Denominacion"
        # (es el que recibe, no la contraparte): en ese caso vale el PDF.
        _den = (r.get("denominacion") or "").strip()
        if _den.upper().startswith("AGRONASAJA"):
            _den = ""
        filas.append({
            # kilos/importes que dice el comprobante de ARCA
            "tn": p.get("tn", 0) or 0,
            "kg": p.get("kg", 0) or 0,
            "importe": p.get("importe", 0) or 0,
            "neto": p.get("neto", 0) or 0,
            "grano": p.get("grano", "") or "",
            "precio_kg": p.get("precio_kg", 0) or 0,
            "ajuste": bool(p.get("ajuste")),
            "tn_merc": round((p.get("kg_mercaderia", 0) or 0) / 1000.0, 3),
            "con_kg": bool(p),
            "coe": coe,
            "fecha": r.get("fecha") or "",
            "consulta": r.get("consulta") or "",
            "tipo": r.get("tipo") or "",
            "flujo": r.get("flujo") or "",
            "lado": r.get("lado") or "",
            "cuit": r.get("cuit") or "",
            "nombre": _den
                      or nombre_cuit.get(r.get("cuit") or "", "")
                      or _contraparte(p)
                      or (f or {}).get("organizacion", ""),
            "sistema": r.get("sistema") or "",
            "operacion": (r.get("operacion") or "").replace("?", "ó"),
            "estado": r.get("estado") or "",
            "sujeto": r.get("sujeto") or "",
            "en_fnn": bool(f),
            "doc_fnn": (f or {}).get("documento", ""),
            "lado_fnn": (f or {}).get("lado", ""),
            "fecha_fnn": (f or {}).get("fecha", ""),
            "org_fnn": (f or {}).get("organizacion", ""),
            "grano_fnn": (f or {}).get("grano", ""),
            "tn_fnn": (f or {}).get("tn", 0) or 0,
        })

    # rango de fechas que efectivamente cubre ARCA (para el control inverso)
    fechas = sorted(x["fecha"] for x in filas if x["fecha"])
    r0, r1 = (fechas[0], fechas[-1]) if fechas else ("", "")

    # ── control inverso: cargado en Finnegans y sin liquidacion en ARCA ─────────
    coes_arca = {x["coe"] for x in filas}
    sin_arca = []
    for coe, f in FC.items():
        if coe in coes_arca:
            continue
        fe = f.get("fecha") or ""
        if not fe or not (r0 <= fe <= r1):     # afuera del rango bajado: no dice nada
            continue
        sin_arca.append({"coe": coe, "fecha": fe, "documento": f.get("documento", ""),
                         "lado": f.get("lado", ""), "organizacion": f.get("organizacion", ""),
                         "grano": f.get("grano", ""), "estado": f.get("estado", ""),
                         "tn": f.get("tn", 0) or 0})
    sin_arca.sort(key=lambda x: x["fecha"], reverse=True)

    # ── COE mal tipeados en Finnegans ──────────────────────────────────────────
    # Si un COE cargado en Finnegans no existe en ARCA pero cambiandole UN digito
    # aparece uno que si existe y con la misma fecha, es un error de tipeo al
    # cargarlo. Aparecio de verdad: LIQPRICPRA - 320 tiene 220132288389 cuando el
    # COE real es 330132288389.
    por_coe_arca = {}
    for x in filas:
        por_coe_arca.setdefault(x["coe"], x)
    def candidatos(coe: str):
        """Variantes de tipeo del COE: un digito cambiado, dos digitos vecinos dados
        vuelta, y el prefijo corregido a uno de los tres validos (3301/3302/3310)."""
        for i in range(12):
            for d in "0123456789":
                if d != coe[i]:
                    yield coe[:i] + d + coe[i + 1:], f"digito {i + 1}"
        for i in range(11):
            if coe[i] != coe[i + 1]:
                yield coe[:i] + coe[i + 1] + coe[i] + coe[i + 2:], f"digitos {i + 1}-{i + 2} dados vuelta"
        for p in ("3301", "3302", "3310"):
            if not coe.startswith(p):
                yield p + coe[4:], f"prefijo {coe[:4]} -> {p}"

    mal_tipeados = []
    for r in sin_arca:
        coe = r["coe"]
        for cand, motivo in candidatos(coe):
            a = por_coe_arca.get(cand)
            if a and a["fecha"] == r["fecha"]:
                # si el COE correcto YA esta cargado en otro documento, ademas del
                # tipeo hay un posible duplicado
                otro = FC.get(a["coe"]) or {}
                mal_tipeados.append({
                    "coe_fnn": coe, "coe_arca": a["coe"], "motivo": motivo,
                    "documento": r["documento"], "fecha": r["fecha"],
                    "organizacion": r["organizacion"] or a["nombre"] or a["cuit"],
                    "lado": r["lado"], "consulta_arca": a["consulta"], "tn": r["tn"],
                    "otro_doc": otro.get("documento", ""),
                    "diagnostico": ("posible duplicado: el COE correcto ya esta en "
                                    + otro["documento"]) if otro.get("documento")
                                   else "COE mal tipeado: el correcto no esta cargado",
                })
                break
    if mal_tipeados:
        log(f"\n[!] {len(mal_tipeados)} COE mal tipeados en Finnegans "
            f"(el de ARCA existe con un digito distinto):")
        for m in mal_tipeados[:15]:
            log(f"      {m['documento']:22s} {m['fecha']}  {m['coe_fnn']} -> {m['coe_arca']} "
                f"({m['motivo']})")
    mt = {m["coe_fnn"] for m in mal_tipeados}
    sin_arca = [r for r in sin_arca if r["coe"] not in mt]

    # esas liquidaciones SI estan cargadas en Finnegans, solo que con el COE mal
    # tipeado: no son "falta ingresar", son "corregir el COE"
    por_arca_typo = {m["coe_arca"]: m for m in mal_tipeados}
    for x in filas:
        m = por_arca_typo.get(x["coe"])
        if m and not x["en_fnn"]:
            x["en_fnn"] = True
            x["typo"] = m["coe_fnn"]
            x["doc_fnn"] = m["documento"]
            x["lado_fnn"] = m["lado"]
            x["tn_fnn"] = m["tn"]

    # las cuatro solapas principales, sin duplicar por las consultas complementarias
    principales = {"lpg_recibidas", "lsg_emitidas", "lpg_emitidas", "lsg_recibidas"}
    act_todas = [x for x in filas if not anulada(x["estado"])]
    falt_unicas = {}
    for x in act_todas:
        if x["en_fnn"]:
            continue
        # una misma liquidacion puede aparecer en dos consultas: la cuento una vez
        ant = falt_unicas.get(x["coe"])
        if ant is None or (ant["consulta"] not in principales and x["consulta"] in principales):
            falt_unicas[x["coe"]] = x
    falt = sorted(falt_unicas.values(), key=lambda x: x["fecha"], reverse=True)

    # ── resumen por solapa ─────────────────────────────────────────────────────
    # los faltantes se cuentan desde la lista deduplicada, para que la tarjeta de
    # cada solapa y el detalle de abajo den el mismo numero
    solapas = []
    for clave, etiqueta, lado, ayuda in SOLAPAS:
        rs = [x for x in filas if x["consulta"] == clave]
        act = [x for x in rs if not anulada(x["estado"])]
        falt_s = [x for x in falt if x["consulta"] == clave]
        solapas.append({
            "clave": clave, "etiqueta": etiqueta, "lado": lado, "ayuda": ayuda,
            "n": len(rs), "activas": len(act), "anuladas": len(rs) - len(act),
            "en_fnn": sum(1 for x in act if x["en_fnn"]), "faltan": len(falt_s),
            "faltan_web": sum(1 for x in falt_s if x["sistema"].upper() == "WEB"),
            # % cruzado: avisa de una si una solapa no cierra (LSG Recibidas cruza poco)
            "pct": round(100.0 * sum(1 for x in act if x["en_fnn"]) / len(act), 1) if act else 0.0,
            "faltan_tn": round(sum(x["tn"] for x in falt_s), 1),
            "faltan_importe": round(sum(x["importe"] for x in falt_s), 2),
            "faltan_sin_kg": sum(1 for x in falt_s if not x["con_kg"]),
            "faltan_ajuste": sum(1 for x in falt_s if x["ajuste"]),
        })
        log(f"    {etiqueta:20s} {len(rs):5d} filas · {len(act):5d} activas · "
            f"{sum(1 for x in act if x['en_fnn']):5d} en Finnegans · {len(falt_s):5d} faltan "
            f"· {sum(x['tn'] for x in falt_s):10,.1f} tn")

    kpi = {
        "arca_coes": len({x["coe"] for x in filas}),
        "fnn_coes": len(FC),
        "activas": len({x["coe"] for x in act_todas}),
        "en_fnn": len({x["coe"] for x in act_todas if x["en_fnn"]}),
        "faltan": len(falt),
        "faltan_venta": sum(1 for x in falt if x["lado"] == "venta"),
        "faltan_compra": sum(1 for x in falt if x["lado"] == "compra"),
        # kilos e importes de lo que falta pasar (del comprobante de ARCA)
        "faltan_tn": round(sum(x["tn"] for x in falt), 1),
        "faltan_tn_venta": round(sum(x["tn"] for x in falt if x["lado"] == "venta"), 1),
        "faltan_tn_compra": round(sum(x["tn"] for x in falt if x["lado"] == "compra"), 1),
        "faltan_tn_primaria": round(sum(x["tn"] for x in falt if x["tipo"] == "primaria"), 1),
        "faltan_tn_secundaria": round(sum(x["tn"] for x in falt if x["tipo"] == "secundaria"), 1),
        "faltan_importe": round(sum(x["importe"] for x in falt), 2),
        "faltan_neto": round(sum(x["neto"] for x in falt), 2),
        "faltan_sin_kg": sum(1 for x in falt if not x["con_kg"]),
        "faltan_ajuste": sum(1 for x in falt if x["ajuste"]),
        "faltan_ajuste_importe": round(sum(x["importe"] for x in falt if x["ajuste"]), 2),
        "sin_arca": len(sin_arca),
        "mal_tipeados": len(mal_tipeados),
        "anuladas": len({x["coe"] for x in filas if anulada(x["estado"])}),
    }
    log(f"\n[=] COE de ARCA: {kpi['arca_coes']} ({kpi['activas']} activos) · "
        f"en Finnegans {kpi['en_fnn']} · FALTAN {kpi['faltan']} "
        f"({kpi['faltan_venta']} de venta, {kpi['faltan_compra']} de compra)")
    log(f"[=] KILOS que faltan pasar: {kpi['faltan_tn']:,.1f} tn "
        f"({kpi['faltan_tn_primaria']:,.1f} primaria + {kpi['faltan_tn_secundaria']:,.1f} secundaria) "
        f"· $ {kpi['faltan_importe']:,.0f}")
    if kpi["faltan_ajuste"]:
        log(f"    de esas, {kpi['faltan_ajuste']} son ajustes de precio (0 tn nuevas) "
            f"por $ {kpi['faltan_ajuste_importe']:,.0f}")
    if kpi["faltan_sin_kg"]:
        log(f"    [!] {kpi['faltan_sin_kg']} sin kilos todavia: corre scripts/arca_liq_kg.py")
    log(f"[=] cargados en Finnegans sin liquidacion en ARCA: {kpi['sin_arca']} (referencial)")
    if falt:
        log("\n    primeras que faltan ingresar:")
        for x in falt[:15]:
            log(f"      {x['fecha']}  {x['coe']}  {x['tipo'][:4]:4s} {x['lado']:6s} "
                f"{x['sistema']:4s} {(x['nombre'] or x['cuit'])[:38]}")
    top = Counter((x["nombre"] or x["cuit"]) for x in falt)
    if top:
        log("\n    faltantes por contraparte:")
        for n, c in top.most_common(12):
            log(f"      {c:4d}  {n[:50]}")

    # el JSON va embebido en index.html: saco las claves vacias para que no pese al doble
    def limpia(rs):
        return [{k: v for k, v in r.items() if v not in ("", None, 0, False)} for r in rs]

    out = {
        "generado": datetime.now().isoformat(timespec="seconds"),
        "bajado_arca": arca.get("bajado"),
        "generado_fnn": fnn.get("generado"),
        "rango_arca": [r0, r1],
        "rango_fnn": fnn.get("rango"),
        "kpi": kpi,
        "solapas": solapas,
        "filas": limpia(filas),
        "faltan": limpia(falt),
        "sin_arca": limpia(sin_arca[:3000]),
        "mal_tipeados": mal_tipeados,
    }
    f = DATA / "arca_liq_cruce.json"
    f.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    log(f"\n[OK] -> {f.name} ({f.stat().st_size / 1024:,.0f} KB)")


if __name__ == "__main__":
    main()
