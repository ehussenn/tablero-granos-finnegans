"""Explora los endpoints de Finnegans relevantes para trazabilidad de COMPRA.
Lista columnas y un par de filas de cada uno para poder diseñar el módulo."""
import sys, os, json
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent))
import finnegans_api as api

# Endpoints a probar
ENDPOINTS = [
    ("/reports/GRANOS_LIQ_COMPRA", {"PARAMWEBREPORT_FechaDesde": "2026-01-01", "PARAMWEBREPORT_FechaHasta": "2030-12-31"}),
    ("/reports/cartaPortePorCTG", {}),  # probar sin params primero
    ("/reports/TrasladoCVByCTG", {}),
    ("/reports/PANEL_VINCULACION_TRANSACCIONES_GRANOS_LIQUIDACIONES", {}),
    ("/reports/USR_TRASGRANCOMPVENT_API", {"PARAMFechaDesde": "2026-01-01", "PARAMFechaHasta": "2030-12-31"}),
    ("/reports/INFORMETRASGRNAPI", {"PARAMFechaDesde": "2026-01-01", "PARAMFechaHasta": "2030-12-31"}),
    ("/reports/situacionFisicaRealGranosCompra", {}),
    ("/reports/analisisRecepcionCompra", {}),
]

for path, params in ENDPOINTS:
    print("="*100)
    print(f">>> {path}")
    print("    params:", params)
    print("="*100)
    try:
        data = api.call(path, params)
        if isinstance(data, list):
            print(f"  ✓ {len(data)} filas")
            if data:
                print(f"  Columnas: {list(data[0].keys())}")
                print(f"  Primera fila:")
                for k, v in data[0].items():
                    print(f"    {k:<45} = {repr(v)[:80]}")
                if len(data) > 1:
                    print(f"  Segunda fila (muestra otro contrato):")
                    for k, v in data[1].items():
                        print(f"    {k:<45} = {repr(v)[:80]}")
        else:
            print(f"  Tipo retornado: {type(data).__name__}")
            print(f"  Valor (primeros 500 chars): {str(data)[:500]}")
    except Exception as e:
        print(f"  [ERROR] {type(e).__name__}: {str(e)[:300]}")
    print()
