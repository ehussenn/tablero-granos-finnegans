"""Deep inspection: 5 tablas DW que vamos a usar.
1) traslado_venta_granos_carta_porte_cruce — primary para trazabilidad (por instrucción del usuario)
2) resumen_de_contrato_de_compra_de_granos
3) resumen_de_contratos_de_venta_de_granos
4) reporte_stock_por_deposito
5) liquidacion_venta_granos
6) composicion_de_saldos (filtrado por canje para no traer 52k filas)"""
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
import psycopg2, psycopg2.extras

conn = psycopg2.connect(
    host=os.environ["FNN_DW_HOST"], dbname="finnegansbi",
    user=os.environ["FNN_DW_USER"], password=os.environ["FNN_DW_PASS"],
    port=5432, sslmode="require", connect_timeout=20,
)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

def dump(t, sample_n=2, filter_sql=""):
    print("\n" + "="*100)
    print(f"TABLA: {t}")
    print("="*100)
    cur.execute(f"SELECT column_name, data_type FROM information_schema.columns WHERE table_name=%s ORDER BY ordinal_position", (t,))
    cols = cur.fetchall()
    print(f"  {len(cols)} columnas: " + ", ".join(c["column_name"] for c in cols))
    cur.execute(f"SELECT * FROM public.{t} {filter_sql} LIMIT {sample_n}")
    for i, r in enumerate(cur.fetchall()):
        print(f"\n  --- Fila {i+1} ---")
        for k, v in r.items():
            vs = repr(v)[:90]
            print(f"    {k:<45} = {vs}")

# 1) Cruce — el que el usuario quiere
# Quiero ver una fila CON CTG (no las de traslado vacío)
dump("agronasajasrl_traslado_venta_granos_carta_porte_cruce", 3, "WHERE numerodocumentoadicional != ''")

# Cuántos CTGs únicos hay
cur.execute("SELECT COUNT(DISTINCT numerodocumentoadicional) c FROM public.agronasajasrl_traslado_venta_granos_carta_porte_cruce WHERE numerodocumentoadicional != ''")
print(f"\n  CTGs únicos en CRUCE (no vacíos): {cur.fetchone()['c']}")

# Cuántos transaccionsubtiponombre distintos?
cur.execute("SELECT transaccionsubtiponombre, COUNT(*) c FROM public.agronasajasrl_traslado_venta_granos_carta_porte_cruce GROUP BY 1 ORDER BY c DESC")
print(f"  Subtipos en CRUCE:")
for r in cur.fetchall():
    print(f"    {r['c']:>6}  {r['transaccionsubtiponombre']}")

# 2) Contratos compra
dump("agronasajasrl_resumen_de_contrato_de_compra_de_granos", 2)

# 3) Contratos venta
dump("agronasajasrl_resumen_de_contratos_de_venta_de_granos", 2)

# 4) Stock por depósito
dump("agronasajasrl_reporte_stock_por_deposito", 2)

# 5) Liquidación venta granos
dump("agronasajasrl_liquidacion_venta_granos", 2)

# 6) Composicion de saldos (filtrada para no traer 52k)
dump("agronasajasrl_composicion_de_saldos", 2, "LIMIT 0")  # solo schema sin filas
print("\n  Sample con canje:")
cur.execute("SELECT * FROM public.agronasajasrl_composicion_de_saldos WHERE condicionpago ILIKE '%canje%' LIMIT 2")
for i, r in enumerate(cur.fetchall()):
    print(f"\n  --- Fila canje {i+1} ---")
    for k, v in r.items():
        if v not in (None, "", 0, "0.0", "0.00"):
            print(f"    {k:<45} = {repr(v)[:90]}")

conn.close()
