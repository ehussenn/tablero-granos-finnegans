# Tablero Granos — Agronasaja

Tablero web de comercialización de granos para Agronasaja, conectado en vivo a la
API REST de Finnegans (Teamplace) y a la API pública de la BCR (Cámara Arbitral
de Cereales) para precios pizarra.

## 🧭 Estructura del tablero

- **COMPRA**
  - Posición General — Tn ajustadas / recibidas / pendientes recibir
  - Financiera — Tn fijadas / liquidadas / Imp Pdte Pagar + Calendario de Pagos manual
  - Canjes — Cruce Composición de Saldos × Contratos Compra × Precios Pizarra BCR
- **VENTA**
  - Posición General — Tn ajustadas / entregadas / pendientes entrega
  - Financiera — Tn fijadas / liquidadas / Imp Pdte Liquidar + Calendario de Cobranzas manual
- **POSICIÓN GENERAL** (pendiente)

## 🛠 Cómo correr localmente

```powershell
# Generar el HTML con datos en vivo:
py build.py

# Abrir el tablero (se genera index.html en la carpeta del proyecto):
Start-Process index.html
```

## 🔌 Fuentes de datos

| Origen | Para qué |
|---|---|
| `api.finneg.com` (OAuth 2.0) | Contratos Venta/Compra, Saldos, Cotizaciones |
| `cac.bcr.com.ar` (público) | Precios pizarra Soja/Maíz/Trigo/Sorgo + TC BNA |

## 📂 Archivos clave

- `build.py` — Script principal que llama APIs y genera `index.html`
- `index.html` — Generado por build (monolítico, datos embebidos como JSON)
- `scripts/finnegans_api.py` — Cliente API Finnegans con cache de token
- `scripts/bcr_pizarra.py` — Scraper de precios pizarra públicos
- `scripts/scraper/` — Playwright (login persistente Finnegans para scraping de vistas web)

## 🔁 Refresh

Pensado para correr con GitHub Actions:
- **Cron diario** — refresca datos y publica a GitHub Pages
- **Manual** — botón "Run workflow" en la web de GitHub

(Workflow pendiente de implementar.)
