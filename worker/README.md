# Worker de acceso — Portal Agronasaja

Este Worker de Cloudflare pone el tablero **detrás de un login** (email + PIN).
Sin sesión válida no se ve nada. Es gratis (Cloudflare Workers free tier).

## 🚀 Despliegue paso a paso (dashboard, sin instalar nada)

### 1. Crear cuenta de Cloudflare (si no tenés)
- Andá a https://dash.cloudflare.com/sign-up
- Registrate con tu email (gratis)

### 2. Crear el Worker
- En el dashboard, menú izquierdo → **Workers & Pages**
- Botón **Create application** → **Create Worker**
- Nombre: `agronasaja-tablero` (o el que quieras) → **Deploy**

### 3. Pegar el código
- Una vez creado → **Edit code**
- Borrá todo lo que viene de ejemplo
- Pegá TODO el contenido de `worker.js`
- Antes de guardar, **editá estas 3 cosas arriba del archivo**:
  - `USUARIOS`: tu email y el PIN que quieras (ej. `"ehussen@agronasaja.com.ar": "2026"`)
  - `SESSION_SECRET`: reemplazá por una cadena larga aleatoria propia
  - `TABLERO_URL`: dejá la de GitHub Pages (ya está puesta)
- Click **Deploy** (arriba a la derecha)

### 4. Probar
- Tu Worker queda en `https://agronasaja-tablero.TU-CUENTA.workers.dev`
- Abrila → te pide login → entrás con tu email + PIN → ves el tablero

### 5. (Opcional) Dominio propio
- Si tenés un dominio en Cloudflare, podés mapear `tablero.agronasaja.com.ar` al Worker
  desde **Workers & Pages → tu worker → Settings → Triggers → Custom Domains**

## 👥 Agregar / quitar usuarios
Editá la constante `USUARIOS` en el código y volvé a **Deploy**:
```js
const USUARIOS = {
  "ehussen@agronasaja.com.ar": "2026",
  "otro@agronasaja.com.ar": "1234",
};
```

## 🔒 Notas de seguridad
- La cookie de sesión está firmada con HMAC: no se puede falsificar.
- La sesión dura 8 horas (configurable en `SESSION_HOURS`).
- El tablero (GitHub Pages) sigue técnicamente accesible si alguien adivina su URL,
  pero esa URL no se difunde ni se indexa en buscadores. Para cierre hermético total,
  el siguiente paso es mover el HTML a Cloudflare KV (avisar para implementarlo).
- Cambiá `SESSION_SECRET` por una cadena propia: si la dejás por defecto, cualquiera
  que vea este código público podría forjar cookies.
