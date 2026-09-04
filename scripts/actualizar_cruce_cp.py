# -*- coding: utf-8 -*-
"""Boton disparador del Cruce CP y del Cruce LIQUIDACIONES
(cartas de porte: pedido del 03/09/2026 · liquidaciones: pedido del 04/09/2026).

Hace todo de una:
  1. Baja de ARCA las CPE de los ultimos dias (Solicitadas + Participante).
  2. Refresca el lado Finnegans (traslados de grano con CTG).
  3. Cruza por CTG y dice, en criollo, QUE CAMIONES SE CARGARON.
  4. Baja de ARCA las liquidaciones primarias y secundarias (emitidas y recibidas).
  5. Refresca los COE de Finnegans, cruza, baja los KILOS de las que faltan
     (del comprobante PDF de ARCA) y dice QUE LIQUIDACIONES SE PASARON.
  6. Regenera el tablero y lo publica (salvo --sin-publicar).

Uso normal (doble click al .cmd del Escritorio) o:
    py scripts/actualizar_cruce_cp.py                # ultimos 45 dias
    py scripts/actualizar_cruce_cp.py --dias 90
    py scripts/actualizar_cruce_cp.py --sin-publicar
    py scripts/actualizar_cruce_cp.py --solo-cruce   # sin tocar ARCA (rapido)
    py scripts/actualizar_cruce_cp.py --sin-liq      # solo cartas de porte
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "scripts"))
CRUCE_JSON = RAIZ / "data" / "arca_cruce.json"
LIQ_JSON = RAIZ / "data" / "arca_liq_cruce.json"

# roles que realmente traen datos de Agronasaja (los demas dan vacio: no se piden)
ROLES_UTILES = ("Destinatario (G/DG);Remitente Comercial Primario (G);"
                "Remitente Comercial Secundario (G);Remitente Comercial Productor (G);"
                "Transportista (G/DG);Representante Recibidor (G);"
                "Pagador Flete Pagador (G/DG)")


def log(*a):
    print(*a, flush=True)


def titulo(t: str):
    log("")
    log("=" * 74)
    log(f"  {t}")
    log("=" * 74)


def corre(cmd: list[str], nombre: str) -> bool:
    log(f"$ {' '.join(cmd[1:])}")
    r = subprocess.run(cmd, cwd=str(RAIZ))
    if r.returncode != 0:
        log(f"[!] {nombre} termino con error {r.returncode}")
        return False
    return True


def faltantes_actuales() -> dict:
    if not CRUCE_JSON.exists():
        return {}
    d = json.loads(CRUCE_JSON.read_text(encoding="utf-8"))
    return {r["CTG"]: r for r in d.get("faltan", [])}


def faltantes_liq() -> dict:
    if not LIQ_JSON.exists():
        return {}
    d = json.loads(LIQ_JSON.read_text(encoding="utf-8"))
    return {r["coe"]: r for r in d.get("faltan", [])}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=45, help="cuantos dias hacia atras revisar en ARCA")
    ap.add_argument("--sin-publicar", action="store_true", dest="sin_publicar")
    ap.add_argument("--solo-cruce", action="store_true", dest="solo_cruce",
                    help="no entra a ARCA: solo refresca Finnegans y vuelve a cruzar")
    ap.add_argument("--sin-liq", action="store_true", dest="sin_liq",
                    help="no toca las liquidaciones (solo el cruce de cartas de porte)")
    a = ap.parse_args()

    t0 = datetime.now()
    log(f"ACTUALIZAR CRUCE CP  ·  {t0.strftime('%d/%m/%Y %H:%M')}")

    # foto de lo que faltaba ANTES, para poder decirle si cargo los camiones
    antes = faltantes_actuales()
    antes_ing = {k: v for k, v in antes.items() if v.get("a_ingresar")}
    log(f"Antes de esta corrida faltaban {len(antes)} camiones "
        f"({len(antes_ing)} de ellos para ingresar de verdad)")
    antes_liq = faltantes_liq()
    if not a.sin_liq:
        log(f"Y faltaban pasar {len(antes_liq)} liquidaciones")

    py = sys.executable

    if not a.solo_cruce:
        titulo("1/6  ARCA — bajando cartas de porte")
        desde = (date.today() - timedelta(days=a.dias)).isoformat()
        ok = corre([py, "scripts/arca_cpe_scraper.py", "--desde", desde,
                    "--solo", "solicitadas", "--forzar"], "ARCA solicitadas")
        ok &= corre([py, "scripts/arca_cpe_scraper.py", "--desde", desde,
                     "--solo", "participantes", "--roles", ROLES_UTILES, "--forzar"],
                    "ARCA participantes")
        corre([py, "scripts/arca_cpe_scraper.py", "--consolidar"], "consolidado ARCA")
        if not ok:
            log("[!] Hubo errores bajando de ARCA — el cruce puede quedar incompleto")
    else:
        titulo("1/6  ARCA — salteado (--solo-cruce)")

    titulo("2/6  Finnegans — traslados de grano con CTG")
    corre([py, "scripts/arca_ctg_cruce.py"], "Finnegans")

    titulo("3/6  Cruce por CTG")
    corre([py, "scripts/arca_ctg_cruce.py", "--cruce"], "cruce")

    # ── comparacion contra la corrida anterior ──────────────────────────────
    ahora = faltantes_actuales()
    cargados = [antes[k] for k in antes if k not in ahora]
    nuevos = [ahora[k] for k in ahora if k not in antes]
    siguen = [ahora[k] for k in ahora if k in antes]
    a_ing = [r for r in ahora.values() if r.get("a_ingresar")]

    titulo("RESULTADO")
    if not antes:
        log("Primera corrida: no hay con que comparar todavia.")
    elif cargados:
        tn = sum(r.get("Kg", 0) for r in cargados) / 1000
        log(f"[OK] CARGASTE {len(cargados)} camiones desde la corrida anterior ({tn:,.1f} tn):")
        for r in sorted(cargados, key=lambda x: x.get("Fecha", ""))[:20]:
            log(f"       CP {r.get('CartaPorte')} · CTG {r.get('CTG')} · {r.get('Fecha')} · "
                f"{r.get('Cultivo')} · {r.get('Kg', 0):,.0f} kg")
        if len(cargados) > 20:
            log(f"       ... y {len(cargados) - 20} mas")
    else:
        log("[--] NO se cargo ninguno de los camiones que faltaban.")

    if nuevos:
        tn = sum(r.get("Kg", 0) for r in nuevos) / 1000
        log("")
        log(f"[+] APARECIERON {len(nuevos)} cartas de porte nuevas sin cargar ({tn:,.1f} tn)")

    log("")
    log(f"AHORA FALTAN: {len(ahora)} camiones en total · "
        f"{len(a_ing)} para ingresar de verdad "
        f"({sum(r.get('Kg', 0) for r in a_ing) / 1000:,.1f} tn)")
    if a_ing:
        log("")
        log("Los que hay que cargar (confirmadas / activas), primeros 25:")
        log(f"   {'CARTA DE PORTE':18s} {'CTG':13s} {'FECHA':11s} {'CULTIVO':10s} {'KG':>9s}  ESTADO")
        for r in sorted(a_ing, key=lambda x: x.get("Fecha", ""))[:25]:
            log(f"   {str(r.get('CartaPorte'))[:18]:18s} {str(r.get('CTG'))[:13]:13s} "
                f"{str(r.get('Fecha'))[:11]:11s} {str(r.get('Cultivo'))[:10]:10s} "
                f"{r.get('Kg', 0):>9,.0f}  {r.get('Estado')}")
        if len(a_ing) > 25:
            log(f"   ... y {len(a_ing) - 25} mas (estan todos en la solapa del tablero)")

    # ── LIQUIDACIONES (LPG + LSG, emitidas y recibidas) ─────────────────────
    if a.sin_liq:
        titulo("4/6 y 5/6  Liquidaciones — salteadas (--sin-liq)")
    else:
        if not a.solo_cruce:
            titulo("4/6  ARCA — bajando liquidaciones primarias y secundarias")
            desde_l = (date.today() - timedelta(days=a.dias)).isoformat()
            corre([py, "scripts/arca_lpg_scraper.py", "--desde", desde_l, "--forzar"],
                  "ARCA liquidaciones")
        else:
            titulo("4/6  ARCA liquidaciones — salteado (--solo-cruce)")
        titulo("5/6  Finnegans — COE de liquidaciones y cruce")
        corre([py, "scripts/finn_liq_coes.py"], "COE Finnegans")
        # primer cruce: define cuales faltan pasar
        corre([py, "scripts/arca_liq_cruce.py"], "cruce liquidaciones")
        if not a.solo_cruce:
            # los kilos no estan en la grilla de ARCA: hay que abrir el comprobante
            # de cada una de las que faltan (queda cacheado en data/arca/_lpg_pdf)
            corre([py, "scripts/arca_liq_kg.py"], "kilos de ARCA")
            # segundo cruce: pega los kilos y los importes al resultado
            corre([py, "scripts/arca_liq_cruce.py"], "cruce liquidaciones con kilos")

        ahora_liq = faltantes_liq()
        pasadas = [antes_liq[k] for k in antes_liq if k not in ahora_liq]
        nuevas_liq = [ahora_liq[k] for k in ahora_liq if k not in antes_liq]
        titulo("RESULTADO LIQUIDACIONES")
        if not antes_liq:
            log("Primera corrida de liquidaciones: no hay con que comparar todavia.")
        elif pasadas:
            log(f"[OK] PASASTE {len(pasadas)} liquidaciones desde la corrida anterior:")
            log(f"     son {sum(r.get('tn', 0) for r in pasadas):,.1f} tn")
            for r in sorted(pasadas, key=lambda x: x.get("fecha", ""))[:20]:
                log(f"       COE {r.get('coe')} · {r.get('fecha')} · {r.get('lado')} · "
                    f"{r.get('tn', 0):,.3f} tn · {(r.get('nombre') or r.get('cuit') or '')[:36]}")
            if len(pasadas) > 20:
                log(f"       ... y {len(pasadas) - 20} mas")
        else:
            log("[--] NO se paso ninguna de las liquidaciones que faltaban.")
        if nuevas_liq:
            log("")
            log(f"[+] APARECIERON {len(nuevas_liq)} liquidaciones nuevas sin pasar")
        log("")
        tn_liq = sum(r.get("tn", 0) for r in ahora_liq.values())
        imp_liq = sum(r.get("importe", 0) for r in ahora_liq.values())
        log(f"AHORA FALTAN PASAR: {len(ahora_liq)} liquidaciones · {tn_liq:,.1f} tn · $ {imp_liq:,.0f} "
            f"({sum(1 for r in ahora_liq.values() if r.get('lado') == 'venta')} de venta, "
            f"{sum(1 for r in ahora_liq.values() if r.get('lado') == 'compra')} de compra)")
        log(f"   primaria {sum(r.get('tn', 0) for r in ahora_liq.values() if r.get('tipo') == 'primaria'):,.1f} tn"
            f" · secundaria {sum(r.get('tn', 0) for r in ahora_liq.values() if r.get('tipo') == 'secundaria'):,.1f} tn")
        if ahora_liq:
            log("")
            log("Las que hay que pasar, primeras 25:")
            log(f"   {'FECHA':11s} {'COE':14s} {'LIQ':11s} {'LADO':7s} {'TN':>10s} {'IMPORTE $':>14s}  CONTRAPARTE")
            for r in sorted(ahora_liq.values(), key=lambda x: x.get("tn", 0), reverse=True)[:25]:
                log(f"   {str(r.get('fecha'))[:11]:11s} {str(r.get('coe'))[:14]:14s} "
                    f"{str(r.get('tipo'))[:11]:11s} {str(r.get('lado'))[:7]:7s} "
                    f"{r.get('tn', 0):>10,.3f} {r.get('importe', 0):>14,.0f}  "
                    f"{(r.get('nombre') or r.get('cuit') or '')[:38]}")
            if len(ahora_liq) > 25:
                log(f"   ... y {len(ahora_liq) - 25} mas (estan todas en la solapa del tablero)")

    if a.sin_publicar:
        titulo("6/6  Tablero — salteado (--sin-publicar)")
    else:
        titulo("6/6  Tablero — regenerando y publicando")
        if corre([py, "build.py"], "build"):
            for cmd, nom in (
                (["git", "add", "data/arca_cruce.json", "data/ctg_finnegans.json",
                  "data/arca/cpe_solicitadas.json", "data/arca/cpe_participantes.json",
                  "data/arca_liq_cruce.json", "data/liq_coes_finnegans.json",
                  "data/arca/lpg_liquidaciones.json", "data/arca/lpg_detalle.json"], "git add"),
                (["git", "commit", "-q", "-m",
                  f"Cruce CP y Liquidaciones ARCA vs Finnegans: datos al {date.today().isoformat()}"],
                 "git commit"),
                (["git", "push", "-q", "origin", "main"], "git push"),
            ):
                corre(cmd, nom)
            log("El tablero se regenera en GitHub y en unos 10 minutos esta en la pagina.")

    log("")
    log(f"Listo en {(datetime.now() - t0).seconds // 60} min "
        f"{(datetime.now() - t0).seconds % 60} seg.")


if __name__ == "__main__":
    main()
