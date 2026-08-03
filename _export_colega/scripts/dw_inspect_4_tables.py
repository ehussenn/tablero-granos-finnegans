"""Inspecciona columnas de las 4 tablas DW que vamos a migrar."""
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
import psycopg2, psycopg2.extras

conn = psycopg2.connect(
    host=os.environ["FNN_DW_HOST"], dbname="finnegansbi",
    user=os.environ["FNN_DW_USER"], password=os.environ["FNN_DW_PASS"],
    port=5432, sslmode="require", connect_timeout=20,
)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

for t in [
    "agronasajasrl_resumen_de_contrato_de_compra_de_granos",
    "agronasajasrl_resumen_de_contratos_de_venta_de_granos",
    "agronasajasrl_reporte_stock_por_deposito",
    "agronasajasrl_composicion_de_saldos",
    "agronasajasrl_liquidacion_venta_granos",
]:
    print("\n" + "="*100)
    print(f"TABLA: {t}")
    print("="*100)
    cur.execute(f"SELECT column_name FROM information_schema.columns WHERE table_name=%s ORDER BY ordinal_position", (t,))
    cols = [r["column_name"] for r in cur.fetchall()]
    print(f"  {len(cols)} columnas: " + ", ".join(cols))

    # 1 fila sample con campos no vacíos
    cur.execute(f"SELECT * FROM public.{t} LIMIT 1")
    rows = cur.fetchall()
    if rows:
        print(f"\n  Sample (1 fila, campos no vacíos):")
        for k, v in rows[0].items():
            if v not in (None, "", "0", "0.0", "0.00", "0.000000"):
                print(f"    {k:<50} = {repr(v)[:80]}")

conn.close()
