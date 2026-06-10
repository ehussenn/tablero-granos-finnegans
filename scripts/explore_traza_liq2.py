"""Segunda pasada: param names correctos."""
import sys
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent))
import finnegans_api as api

# Param names sin prefijo PARAM
print("="*100); print("cartaPortePorCTG (param CTG, sin prefijo)"); print("="*100)
try:
    data = api.call("/reports/cartaPortePorCTG", {"CTG": "10128658870"})
    if isinstance(data, list):
        print(f"✓ {len(data)} filas")
        if data:
            print(f"Columnas: {list(data[0].keys())}")
            for k, v in data[0].items():
                print(f"  {k:<45} = {repr(v)[:100]}")
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {str(e)[:300]}")

print("\n\n" + "="*100); print("TrasladoCVByCTG (param CTG, sin prefijo)"); print("="*100)
try:
    data = api.call("/reports/TrasladoCVByCTG", {"CTG": "10128658870"})
    if isinstance(data, list):
        print(f"✓ {len(data)} filas")
        if data:
            print(f"Columnas: {list(data[0].keys())}")
            for i, r in enumerate(data[:2]):
                print(f"\n  Fila {i+1}:")
                for k, v in r.items():
                    print(f"    {k:<45} = {repr(v)[:100]}")
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {str(e)[:300]}")

# Probemos getLiqTipoFromCoe con un COE inventado para ver el formato de respuesta
print("\n\n" + "="*100); print("getLiqTipoFromCoe (COE=dummy para ver error/respuesta)"); print("="*100)
try:
    data = api.call("/reports/getLiqTipoFromCoe", {"COE": "330100000000"})
    print(f"Resultado tipo: {type(data).__name__}")
    if isinstance(data, list):
        print(f"  {len(data)} filas")
        if data: print(f"  Cols: {list(data[0].keys())}")
    elif isinstance(data, dict):
        print(f"  Keys: {list(data.keys())}")
        for k, v in data.items(): print(f"    {k:<35} = {repr(v)[:80]}")
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {str(e)[:300]}")

# Trasladospendientes from contrato (COMP138 que vimos)
print("\n\n" + "="*100); print("TrasladosPendientesFromContrato (COMP138)"); print("="*100)
try:
    data = api.call("/reports/TrasladosPendientesFromContrato", {"Contrato": "COMP138"})
    print(f"Tipo: {type(data).__name__}")
    if isinstance(data, list) and data:
        print(f"Cols: {list(data[0].keys())}")
        for k, v in data[0].items(): print(f"  {k:<45} = {repr(v)[:100]}")
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {str(e)[:300]}")

# USR_TRASGRANCOMPVENT_API con ContratoCompraNombre
print("\n\n" + "="*100); print("USR_TRASGRANCOMPVENT_API (COMP138)"); print("="*100)
try:
    data = api.call("/reports/USR_TRASGRANCOMPVENT_API", {"ContratoCompraNombre": "COMP138"})
    print(f"Tipo: {type(data).__name__}")
    if isinstance(data, list):
        print(f"✓ {len(data)} filas")
        if data:
            print(f"Columnas: {list(data[0].keys())}")
            for k, v in data[0].items():
                print(f"  {k:<45} = {repr(v)[:100]}")
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {str(e)[:300]}")
