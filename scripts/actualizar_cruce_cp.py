# -*- coding: utf-8 -*-
"""Boton disparador del Cruce CP (pedido del usuario 03/09/2026).

Hace todo de una:
  1. Baja de ARCA las CPE de los ultimos dias (Solicitadas + Participante).
  2. Refresca el lado Finnegans (traslados de grano con CTG).
  3. Cruza por CTG y compara contra la corrida anterior para decir, en criollo,
     QUE CAMIONES SE CARGARON, cuales siguen faltando y cuales aparecieron nuevos.
  4. Regenera el tablero y lo publica (salvo --sin-publicar).

Uso normal (doble click al .cmd del Escritorio) o:
    py scripts/actualizar_cruce_cp.py                # ultimos 45 dias
    py scripts/actualizar_cruce_cp.py --dias 90
    py scripts/actualizar_cruce_cp.py --sin-publicar
    py scripts/actualizar_cruce_cp.py --solo-cruce   # sin tocar ARCA (rapido)
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


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=45, help="cuantos dias hacia atras revisar en ARCA")
    ap.add_argument("--sin-publicar", action="store_true", dest="sin_publicar")
    ap.add_argument("--solo-cruce", action="store_true", dest="solo_cruce",
                    help="no entra a ARCA: solo refresca Finnegans y vuelve a cruzar")
    a = ap.parse_args()

    t0 = datetime.now()
    log(f"ACTUALIZAR CRUCE CP  ·  {t0.strftime('%d/%m/%Y %H:%M')}")

    # foto de lo que faltaba ANTES, para poder decirle si cargo los camiones
    antes = faltantes_actuales()
    antes_ing = {k: v for k, v in antes.items() if v.get("a_ingresar")}
    log(f"Antes de esta corrida faltaban {len(antes)} camiones "
        f"({len(antes_ing)} de ellos para ingresar de verdad)")

    py = sys.executable

    if not a.solo_cruce:
        titulo("1/4  ARCA — bajando cartas de porte")
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
        titulo("1/4  ARCA — salteado (--solo-cruce)")

    titulo("2/4  Finnegans — traslados de grano con CTG")
    corre([py, "scripts/arca_ctg_cruce.py"], "Finnegans")

    titulo("3/4  Cruce por CTG")
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

    if a.sin_publicar:
        titulo("4/4  Tablero — salteado (--sin-publicar)")
    else:
        titulo("4/4  Tablero — regenerando y publicando")
        if corre([py, "build.py"], "build"):
            for cmd, nom in (
                (["git", "add", "data/arca_cruce.json", "data/ctg_finnegans.json",
                  "data/arca/cpe_solicitadas.json", "data/arca/cpe_participantes.json"], "git add"),
                (["git", "commit", "-q", "-m",
                  f"Cruce CP ARCA vs Finnegans: datos al {date.today().isoformat()}"], "git commit"),
                (["git", "push", "-q", "origin", "main"], "git push"),
            ):
                corre(cmd, nom)
            log("El tablero se regenera en GitHub y en unos 10 minutos esta en la pagina.")

    log("")
    log(f"Listo en {(datetime.now() - t0).seconds // 60} min "
        f"{(datetime.now() - t0).seconds % 60} seg.")


if __name__ == "__main__":
    main()
