"""Verifica si el ContractNumber de LDC (ej 001CV208011721) matchea con algo en el DW."""
import os, sys, json
from pathlib import Path
from decimal import Decimal
from datetime import date, datetime
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

# Cargar contratos LDC
ldc_settle = json.loads((ROOT/"data/ldc/settlements.json").read_text(encoding="utf-8"))
ldc_contracts = list({s["ContractNumber"] for s in ldc_settle["List"] if s.get("ContractNumber")})
print(f"[+] {len(ldc_contracts)} ContractNumber únicos en LDC settlements")
print(f"    sample: {ldc_contracts[:5]}")

conn = psycopg2.connect(
    host=os.environ["FNN_DW_HOST"], user=os.environ["FNN_DW_USER"],
    password=os.environ["FNN_DW_PASS"], dbname=os.environ.get("FNN_DW_DB","finnegansbi"),
    connect_timeout=30,
)
cur = conn.cursor()

# Buscar esos numeros en agronasajasrl_traslado_de_granos
print("\n[+] Buscando ContractNumbers LDC en traslado_de_granos...")
matches_by_col = {}
for col in ["numerocontratointermediario", "numerodocumentocontrato", "numerocontratocorredor", "numerodocumentoadicional", "documento"]:
    placeholders = ",".join(["%s"] * len(ldc_contracts))
    try:
        cur.execute(f"""
            SELECT DISTINCT {col}, numerodocumentoadicional, nombrecontrato
            FROM agronasajasrl_traslado_de_granos
            WHERE {col} IN ({placeholders})
            LIMIT 50
        """, ldc_contracts)
        rows = cur.fetchall()
        if rows:
            matches_by_col[col] = rows
            print(f"  ✓ {col}: {len(rows)} matches")
            for r in rows[:5]: print(f"      {r}")
    except Exception as e: pass

# Buscar también en el cruce
print("\n[+] Buscando en cruce CTG-carta porte...")
for col in ["numerodocumentoadicional", "documento", "nombrecontrato", "numerodocumento"]:
    try:
        placeholders = ",".join(["%s"] * len(ldc_contracts))
        cur.execute(f"""
            SELECT DISTINCT {col}, numerodocumentoadicional, nombrecontrato, destino
            FROM agronasajasrl_traslado_venta_granos_carta_porte_cruce
            WHERE {col} IN ({placeholders})
            LIMIT 50
        """, ldc_contracts)
        rows = cur.fetchall()
        if rows:
            print(f"  ✓ {col}: {len(rows)} matches")
            for r in rows[:5]: print(f"      {r}")
    except: pass

# Si no hay match directo, ver el numerocontratointermediario de los traslados LDC
print("\n[+] Inspeccionar numerocontratointermediario en traslados con destino LDC...")
cur.execute("""
    SELECT DISTINCT numerocontratointermediario, COUNT(*)
    FROM agronasajasrl_traslado_de_granos
    WHERE UPPER(destinatario) LIKE '%LDC%' OR UPPER(destinatario) LIKE '%DREYFUS%'
       OR UPPER(organizacionnombre) LIKE '%LDC%'
    GROUP BY numerocontratointermediario
    ORDER BY COUNT(*) DESC LIMIT 30
""")
print("  numerocontratointermediario / count:")
for v, c in cur.fetchall(): print(f"    {c:4d}  {v}")

cur.close(); conn.close()
