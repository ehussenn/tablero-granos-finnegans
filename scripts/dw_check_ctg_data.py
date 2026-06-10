"""Chequear qué data realmente tiene un CTG en el DW (todas las tablas)."""
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
import psycopg2, psycopg2.extras
conn = psycopg2.connect(
    host=os.environ["FNN_DW_HOST"], dbname="finnegansbi",
    user=os.environ["FNN_DW_USER"], password=os.environ["FNN_DW_PASS"],
    port=5432, sslmode="require", connect_timeout=20,
)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
CTG = "10131954072"

# Buscar en traslado_de_granos (la fuente principal de la Trazabilidad)
print("="*80); print(f"agronasajasrl_traslado_de_granos — CTG {CTG}"); print("="*80)
cur.execute("""SELECT * FROM public.agronasajasrl_traslado_de_granos
               WHERE numerodocumentoadicional = %s""", (CTG,))
rows = cur.fetchall()
print(f"  {len(rows)} filas\n")
for i, r in enumerate(rows):
    print(f"--- Fila {i+1} (operaciontipo={r.get('operaciontipo')}, subtipo={r.get('transaccionsubtiponombre')}) ---")
    for k, v in r.items():
        if v not in (None, "", "0", "0.0", "0.000000", "0.0000"):
            print(f"  {k:<45} = {repr(v)[:70]}")

# Y en la otra tabla
print("\n"+"="*80); print(f"agronasajasrl_traslado_venta_granos_carta_porte_cruce — CTG {CTG}"); print("="*80)
cur.execute("""SELECT * FROM public.agronasajasrl_traslado_venta_granos_carta_porte_cruce
               WHERE numerodocumentoadicional = %s""", (CTG,))
rows = cur.fetchall()
print(f"  {len(rows)} filas\n")
for i, r in enumerate(rows):
    print(f"--- Fila {i+1} (subtipo={r.get('transaccionsubtiponombre')}) ---")
    for k, v in r.items():
        if v not in (None, "", "0", "0.0", "0.000000", "0.0000"):
            print(f"  {k:<45} = {repr(v)[:70]}")

conn.close()
