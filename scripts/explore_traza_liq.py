"""Sigue explorando: PANEL_VINCULACION y cartaPortePorCTG con params correctos."""
import sys
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent))
import finnegans_api as api

# 1) PANEL_VINCULACION con CompraVenta=Compra
print("="*100); print("PANEL_VINCULACION_TRANSACCIONES_GRANOS_LIQUIDACIONES (Compra)"); print("="*100)
try:
    data = api.call("/reports/PANEL_VINCULACION_TRANSACCIONES_GRANOS_LIQUIDACIONES",
                    {"PARAMWEBREPORT_CompraVenta": "Compra"})
    if isinstance(data, list):
        print(f"✓ {len(data)} filas")
        if data:
            print(f"Columnas: {list(data[0].keys())}")
            for k, v in data[0].items():
                print(f"  {k:<45} = {repr(v)[:80]}")
            print("\n2da fila:")
            if len(data) > 1:
                for k, v in data[1].items():
                    print(f"  {k:<45} = {repr(v)[:80]}")
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {str(e)[:300]}")

# 2) cartaPortePorCTG con un CTG real (10128658870 que vi en INFORMETRASGRNAPI)
print("\n\n" + "="*100); print("cartaPortePorCTG (CTG=10128658870)"); print("="*100)
try:
    data = api.call("/reports/cartaPortePorCTG", {"PARAMCTG": "10128658870"})
    if isinstance(data, list):
        print(f"✓ {len(data)} filas")
        if data:
            print(f"Columnas: {list(data[0].keys())}")
            for k, v in data[0].items():
                print(f"  {k:<45} = {repr(v)[:80]}")
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {str(e)[:300]}")

# 3) TrasladoCVByCTG con un CTG
print("\n\n" + "="*100); print("TrasladoCVByCTG (CTG=10128658870)"); print("="*100)
try:
    data = api.call("/reports/TrasladoCVByCTG", {"PARAMCTG": "10128658870"})
    if isinstance(data, list):
        print(f"✓ {len(data)} filas")
        if data:
            print(f"Columnas: {list(data[0].keys())}")
            for k, v in data[0].items():
                print(f"  {k:<45} = {repr(v)[:80]}")
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {str(e)[:300]}")

# 4) LiquidacionCompraGranos (listado)
print("\n\n" + "="*100); print("LiquidacionCompraGranos (listado SIN code)"); print("="*100)
try:
    data = api.call("/LiquidacionCompraGranos", {})
    if isinstance(data, list):
        print(f"✓ {len(data)} filas")
        if data:
            print(f"Columnas: {list(data[0].keys())}")
    elif isinstance(data, dict):
        print(f"Dict keys: {list(data.keys())[:20]}")
        for k, v in list(data.items())[:30]:
            print(f"  {k:<35} = {repr(v)[:80]}")
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {str(e)[:300]}")

# 5) Probar GRANOS_LIQ_COMPRA sin filtrar fechas
print("\n\n" + "="*100); print("GRANOS_LIQ_COMPRA (mas amplio)"); print("="*100)
try:
    data = api.call("/reports/GRANOS_LIQ_COMPRA", {"PARAMWEBREPORT_FechaDesde":"2023-01-01","PARAMWEBREPORT_FechaHasta":"2030-12-31"})
    if isinstance(data, list):
        print(f"✓ {len(data)} filas")
        if data:
            print(f"Sample IDs: {[r.get('ID') for r in data[:10]]}")
            print(f"Sample NUMEROs: {[r.get('NUMERO') for r in data[:10]]}")
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {str(e)[:300]}")
