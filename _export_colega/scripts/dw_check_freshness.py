"""Compara fechas máximas en DW vs lo que tenemos publicado."""
import os, sys
from pathlib import Path
from datetime import datetime
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

conn = psycopg2.connect(host=os.environ["FNN_DW_HOST"], user=os.environ["FNN_DW_USER"],
    password=os.environ["FNN_DW_PASS"], dbname="finnegansbi", connect_timeout=30)
cur = conn.cursor()

print("="*70)
print(f"AHORA (Argentina): {datetime.now()}")
print("="*70)

# Contratos compra
print("\n[+] agronasajasrl_resumen_de_contrato_de_compra_de_granos:")
cur.execute("SELECT MAX(fecha), COUNT(*), MAX(fecha)::date - CURRENT_DATE FROM agronasajasrl_resumen_de_contrato_de_compra_de_granos")
maxf, cnt, diff = cur.fetchone()
print(f"   última fecha contrato: {maxf}  ({cnt:,} total) | diff vs hoy: {diff} días")
cur.execute("""SELECT COUNT(*) FROM agronasajasrl_resumen_de_contrato_de_compra_de_granos
                WHERE fecha::timestamp >= CURRENT_DATE - INTERVAL '7 days'""")
print(f"   contratos compra últimos 7 días: {cur.fetchone()[0]}")

# Contratos venta
print("\n[+] agronasajasrl_resumen_de_contratos_de_venta_de_granos:")
cur.execute("SELECT MAX(fecha), COUNT(*), MAX(fecha)::date - CURRENT_DATE FROM agronasajasrl_resumen_de_contratos_de_venta_de_granos")
maxf, cnt, diff = cur.fetchone()
print(f"   última fecha venta: {maxf}  ({cnt:,} total) | diff: {diff} días")
cur.execute("""SELECT COUNT(*) FROM agronasajasrl_resumen_de_contratos_de_venta_de_granos
                WHERE fecha::timestamp >= CURRENT_DATE - INTERVAL '7 days'""")
print(f"   contratos venta últimos 7 días: {cur.fetchone()[0]}")

# Traslados (CTGs)
print("\n[+] agronasajasrl_traslado_venta_granos_carta_porte_cruce:")
cur.execute("SELECT MAX(fecha), COUNT(*), MAX(fecha)::date - CURRENT_DATE FROM agronasajasrl_traslado_venta_granos_carta_porte_cruce")
maxf, cnt, diff = cur.fetchone()
print(f"   última fecha traslado: {maxf}  ({cnt:,} total) | diff: {diff} días")

# Composición de saldo (esta tabla puede no existir en DW, viene del API REST)
print("\n[+] Buscando tablas de saldos/canje en DW...")
cur.execute("""SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND
                (table_name ILIKE '%saldo%' OR table_name ILIKE '%canje%' OR table_name ILIKE '%composicion%')
                ORDER BY table_name""")
for (t,) in cur.fetchall(): print(f"   {t}")

cur.close(); conn.close()
