# -*- coding: utf-8 -*-
"""Respaldo de lo que se carga a mano en el tablero.  SOLO LECTURA.

Pedido del usuario (16/09/2026): "lo que es carga manual al tablero son cálculos
diarios que hago, así que no necesito un backup sólo en Posición Granaria. Sí
hacéme backup en pagos, lo que envío a liquidar y todo el resto."

Qué respalda (todo GET contra el Worker, no escribe nada en la nube):
    pagos              Proyectado de Pagos
    contratos          Códigos de Contratos
    finales_estado     semáforo de Finales Pendientes (enviadas / hechas)
    envios_liq         lo que se mandó a liquidar, con sus condiciones
    marcas_liq         marcas a mano de liquidado / enviado
    comerciales_admin  la relación comercial -> administrativa y los correos

Qué NO respalda, a pedido expreso:
    pn_manual          las ediciones de la Posición Granaria son cálculos del día

Deja dos cosas en data/respaldos/:
    respaldo_AAAA-MM-DD.json   la foto del día (una por día, se pisa si se corre
                               varias veces)
    ultimo.json                siempre la más reciente, para mirar rápido
Y una carpeta por clave con el historial, para poder volver a cualquier día.

Uso:
    py scripts/respaldo_tablero.py              # respalda
    py scripts/respaldo_tablero.py --ver        # solo muestra qué hay, sin guardar
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

RAIZ = Path(__file__).resolve().parent.parent
OUT = RAIZ / "data" / "respaldos"
ART = timezone(timedelta(hours=-3))

WORKER = "https://tablero-agronasaja.ehussen.workers.dev/api/data/"
# El Worker acepta la identidad por header cuando el origen es el del tablero
# (misma puerta que usa la vista embebida en la extranet). Es solo lectura.
HD = {"User-Agent": "respaldo-tablero (solo lectura)",
      "Origin": "https://ehussenn.github.io",
      "Referer": "https://ehussenn.github.io/",
      "X-Tablero-User": "ehussen@agronasaja.com.ar"}

CLAVES = [
    ("pagos",             "Proyectado de Pagos"),
    ("contratos",         "Códigos de Contratos"),
    ("finales_estado",    "Finales Pendientes (semáforo)"),
    ("envios_liq",        "Envíos a liquidar"),
    ("marcas_liq",        "Marcas de liquidado / enviado"),
    ("comerciales_admin", "Comerciales, administrativas y correos"),
]
# Pedido del usuario: la Posición Granaria manual no se respalda (cálculos del día)
NO_RESPALDAR = {"pn_manual"}


def baja(clave: str):
    r = urllib.request.urlopen(urllib.request.Request(WORKER + clave, headers=HD), timeout=120)
    raw = r.read().decode("utf-8", "ignore")
    return json.loads(raw), len(raw)


def cuantos(d) -> str:
    if isinstance(d, list):
        return f"{len(d)} registro(s)"
    if isinstance(d, dict):
        return f"{len(d)} entrada(s)"
    return "—"


def main() -> int:
    solo_ver = "--ver" in sys.argv
    hoy = datetime.now(ART)
    foto = {"generado": hoy.isoformat(timespec="seconds"), "claves": {}}
    print(f"RESPALDO DEL TABLERO · {hoy.strftime('%d/%m/%Y %H:%M')} hs (hora argentina)\n")
    print(f"  {'CLAVE':20s} {'QUÉ ES':40s} {'CONTENIDO':>18s} {'TAMAÑO':>10s}")
    total = fallos = 0
    for clave, que in CLAVES:
        try:
            d, n = baja(clave)
        except urllib.error.HTTPError as e:
            print(f"  {clave:20s} {que:40s} {'HTTP ' + str(e.code):>18s}")
            fallos += 1
            continue
        except Exception as e:
            print(f"  {clave:20s} {que:40s} {type(e).__name__:>18s}")
            fallos += 1
            continue
        foto["claves"][clave] = d
        total += n
        print(f"  {clave:20s} {que:40s} {cuantos(d):>18s} {n:>9,} B")
    print(f"\n  no se respalda a pedido: {', '.join(sorted(NO_RESPALDAR))} "
          f"(son cálculos del día)")

    if solo_ver:
        print("\n  (--ver: no se guardó nada)")
        return 0
    if not foto["claves"]:
        print("\n  [!] no pude bajar ninguna clave, no guardo nada")
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    dia = hoy.strftime("%Y-%m-%d")
    (OUT / f"respaldo_{dia}.json").write_text(
        json.dumps(foto, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "ultimo.json").write_text(
        json.dumps(foto, ensure_ascii=False, indent=1), encoding="utf-8")
    # y una copia por clave, para poder mirar una sola sin abrir todo
    for clave, d in foto["claves"].items():
        c = OUT / clave
        c.mkdir(exist_ok=True)
        (c / f"{dia}.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")

    # no dejar que crezca para siempre: se guardan las ultimas 120 fotos diarias
    for viejo in sorted(OUT.glob("respaldo_*.json"))[:-120]:
        try:
            viejo.unlink()
        except Exception:
            pass
    for clave, _ in CLAVES:
        for viejo in sorted((OUT / clave).glob("*.json"))[:-120]:
            try:
                viejo.unlink()
            except Exception:
                pass

    print(f"\n[OK] guardado en data/respaldos/ · {total:,} bytes"
          + (f" · {fallos} clave(s) sin respuesta" if fallos else ""))
    print("     Se versiona en GitHub, asi que queda respaldado fuera de esta maquina.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
