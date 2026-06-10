"""Prueba distintos valores para PARAMWEBREPORT_MonedaID en USR_RESSTOCKDEP."""
from __future__ import annotations
import sys, json
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import finnegans_api as api

MONEDAS_TRY = ["", "$", "ARS", "PESO", "PES", "1", "2", "P", "AR$", "ARSPES", "ARGENTINA"]

print("[+] Probando moneda codes...", flush=True)
for m in MONEDAS_TRY:
    params = {"PARAMWEBREPORT_fecha":"getCurrentDate"}
    if m: params["PARAMWEBREPORT_MonedaID"] = m
    try:
        r = api.call("/reports/USR_RESSTOCKDEP", params)
        if isinstance(r, list):
            print(f"  [OK] moneda='{m}' -> {len(r)} filas")
            if r:
                print(f"    keys: {list(r[0].keys())[:25]}")
                # mostrar primeras 2 filas resumen
                for row in r[:2]:
                    deposito = row.get("DEPOSITO") or row.get("DEPOSITONOMBRE") or row.get("NOMBREDEPOSITO") or "?"
                    prod = row.get("PRODUCTO") or row.get("PRODUCTONOMBRE") or row.get("ARTICULO") or "?"
                    qty = row.get("CANTIDAD") or row.get("STOCK") or row.get("EXISTENCIA") or row.get("CANTIDADTOTAL") or "?"
                    print(f"    ej: dep='{deposito}' prod='{prod}' qty={qty}")
            break
        else:
            print(f"  [.] moneda='{m}' -> respuesta no-lista: {str(r)[:80]}")
    except Exception as e:
        msg = str(e)[:140]
        print(f"  [!] moneda='{m}' -> {msg}")
