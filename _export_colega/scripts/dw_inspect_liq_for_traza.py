"""Inspecciona las tablas de liquidacion para ver si tienen CTG/COE y poder linkar con Trazabilidad."""
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
import psycopg2, psycopg2.extras

conn = psycopg2.connect(
    host=os.environ["FNN_DW_HOST"], dbname="finnegansbi",
    user=os.environ["FNN_DW_USER"], password=os.environ["FNN_DW_PASS"],
    port=5432, sslmode="require", connect_timeout=20,
)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

for t in ["agronasajasrl_liquidacion_venta_granos",
          "agronasajasrl_liquidaciongranos",
          "agronasajasrl_liquidacionventagranos"]:
    print("\n" + "="*100); print(f"TABLA: {t}"); print("="*100)
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s ORDER BY ordinal_position", (t,))
    cols = [r["column_name"] for r in cur.fetchall()]
    cur.execute(f"SELECT COUNT(*) c FROM public.{t}")
    n = cur.fetchone()['c']
    print(f"  {n} filas | {len(cols)} columnas")
    # Buscar columnas relacionadas con CTG/COE/CP/factor/comision
    print(f"  Cols clave:")
    for c in cols:
        if any(kw in c.lower() for kw in ("ctg","coe","cartaporte","carta_de_porte","numerodocumentoadicional",
                                          "factor","comision","gasto","retencion","iva","kilos","ganancia",
                                          "transaccion","contrato","liquidacion","percep","ret","fecha")):
            print(f"    • {c}")
    # Sample 1 fila con campos clave
    cur.execute(f"SELECT * FROM public.{t} LIMIT 2")
    for i, r in enumerate(cur.fetchall()):
        print(f"\n  --- Fila {i+1} (campos no vacios) ---")
        for k, v in r.items():
            if v not in (None, "", "0", "0.0", "0.00", "0.0000", "0.000000"):
                print(f"    {k:<50} = {repr(v)[:80]}")

# Buscar columnas que puedan tener CTG en cualquier tabla
print("\n\n" + "="*100); print("COLUMNAS CON 'ctg' EN CUALQUIER TABLA DW"); print("="*100)
cur.execute("""SELECT table_name, column_name FROM information_schema.columns
               WHERE table_schema='public' AND table_name LIKE 'agronasajasrl_%'
                 AND (LOWER(column_name) LIKE '%ctg%' OR LOWER(column_name) LIKE '%coe%'
                      OR LOWER(column_name) LIKE '%cartaporte%' OR LOWER(column_name) LIKE '%carta_de_porte%')
               ORDER BY table_name""")
for r in cur.fetchall():
    print(f"  {r['table_name']:<60} .{r['column_name']}")

conn.close()
