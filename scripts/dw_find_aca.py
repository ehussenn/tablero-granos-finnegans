"""Busca ACA / cooperativas en DW."""
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

conn = psycopg2.connect(
    host=os.environ["FNN_DW_HOST"], user=os.environ["FNN_DW_USER"],
    password=os.environ["FNN_DW_PASS"], dbname="finnegansbi", connect_timeout=30,
)
cur = conn.cursor()

# Buscar ACA/cooperativa en destino del cruce
for col in ["destino", "organizacionnombre", "representante", "titular"]:
    cur.execute(f"""
        SELECT DISTINCT {col}, COUNT(*) FROM agronasajasrl_traslado_venta_granos_carta_porte_cruce
        WHERE UPPER({col}::text) LIKE '%ACA%' OR UPPER({col}::text) LIKE '%COOPERATIVA%'
           OR UPPER({col}::text) LIKE '%COOP%'
        GROUP BY {col} ORDER BY COUNT(*) DESC LIMIT 20
    """)
    r = cur.fetchall()
    if r:
        print(f"\n[{col}] ACA/COOP en cruce:")
        for v, c in r: print(f"  {c:5d}  {v}")

cur.close(); conn.close()
