"""Busca LDC en contratos de VENTA + cruce."""
import os, sys, json
from pathlib import Path
import psycopg2
sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path(__file__).resolve().parent.parent
env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

OUT = ROOT / "data" / "ldc"
OUT.mkdir(parents=True, exist_ok=True)

conn = psycopg2.connect(
    host=os.environ["FNN_DW_HOST"], user=os.environ["FNN_DW_USER"],
    password=os.environ["FNN_DW_PASS"], dbname=os.environ.get("FNN_DW_DB","finnegansbi"),
    connect_timeout=30,
)
cur = conn.cursor()

# Cols de ventas
cur.execute("""
    SELECT column_name FROM information_schema.columns
    WHERE table_name = 'agronasajasrl_resumen_de_contratos_de_venta_de_granos'
    ORDER BY ordinal_position
""")
v_cols = [r[0] for r in cur.fetchall()]
print(f"[+] Cols VENTA ({len(v_cols)}): {v_cols}")

# Cols del cruce
cur.execute("""
    SELECT column_name FROM information_schema.columns
    WHERE table_name = 'agronasajasrl_traslado_venta_granos_carta_porte_cruce'
    ORDER BY ordinal_position
""")
c_cols = [r[0] for r in cur.fetchall()]
print(f"\n[+] Cols CRUCE ({len(c_cols)}): {c_cols}")

# Buscar LDC en todas las cols de VENTA
print(f"\n[+] Buscando LDC en tabla VENTA...")
for col in v_cols:
    try:
        cur.execute(f"""
            SELECT DISTINCT {col}, COUNT(*) FROM agronasajasrl_resumen_de_contratos_de_venta_de_granos
            WHERE UPPER({col}::text) LIKE '%LDC%' OR UPPER({col}::text) LIKE '%DREYFUS%' OR UPPER({col}::text) LIKE '%LOUIS%'
            GROUP BY {col} ORDER BY COUNT(*) DESC LIMIT 10
        """)
        r = cur.fetchall()
        if r:
            print(f"  col {col}:")
            for v, c in r: print(f"    {c:5d}  {v}")
    except: pass

# Buscar LDC en cruce
print(f"\n[+] Buscando LDC en cruce...")
for col in c_cols:
    try:
        cur.execute(f"""
            SELECT DISTINCT {col}, COUNT(*) FROM agronasajasrl_traslado_venta_granos_carta_porte_cruce
            WHERE UPPER({col}::text) LIKE '%LDC%' OR UPPER({col}::text) LIKE '%DREYFUS%' OR UPPER({col}::text) LIKE '%LOUIS%'
            GROUP BY {col} ORDER BY COUNT(*) DESC LIMIT 10
        """)
        r = cur.fetchall()
        if r:
            print(f"  col {col}:")
            for v, c in r: print(f"    {c:5d}  {v}")
    except: pass

cur.close(); conn.close()
