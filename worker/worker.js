/**
 * Worker de Cloudflare — Portal de acceso al Tablero Granos Agronasaja.
 *
 * Qué hace:
 *  1. Muestra una pantalla de login (email + PIN)
 *  2. Valida contra la lista USUARIOS de abajo
 *  3. Si OK, setea una cookie de sesión firmada (HMAC, no falsificable)
 *  4. Sirve el tablero (lo trae de GitHub Pages) solo a usuarios autenticados
 *  5. Sin sesión válida -> muestra el login
 *
 * Cómo configurar (editás las constantes de abajo):
 *  - USUARIOS: emails y PINs autorizados
 *  - SESSION_SECRET: una cadena larga aleatoria (cambiala por una tuya)
 *  - TABLERO_URL: la URL del tablero en GitHub Pages (no la difundas)
 */

// ====== CONFIGURACIÓN ======
// Los valores sensibles (USUARIOS y SESSION_SECRET) se inyectan como
// SECRETS de Cloudflare (env), NO se guardan en este archivo público.
// Fallbacks de respaldo por si no se setean (NO usar en producción):
const USUARIOS_FALLBACK = {};
const SESSION_SECRET_FALLBACK = "fallback-no-usar";

// URL del tablero (GitHub Pages). El Worker la trae internamente; no la difundas.
const TABLERO_URL = "https://ehussenn.github.io/tablero-granos-finnegans/";

// Duración de la sesión (horas)
const SESSION_HOURS = 8;
// ============================

const COOKIE_NAME = "agronasaja_sess";

// ---- helpers de firma (HMAC-SHA256 via Web Crypto) ----
async function hmac(data, secret) {
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]
  );
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(data));
  return btoa(String.fromCharCode(...new Uint8Array(sig))).replace(/=+$/, "");
}

async function crearToken(email, secret) {
  const exp = Date.now() + SESSION_HOURS * 3600 * 1000;
  const payload = `${email}|${exp}`;
  const firma = await hmac(payload, secret);
  return btoa(payload).replace(/=+$/, "") + "." + firma;
}

async function validarToken(token, secret) {
  if (!token || !token.includes(".")) return null;
  const [payloadB64, firma] = token.split(".");
  let payload;
  try { payload = atob(payloadB64); } catch { return null; }
  const firmaEsperada = await hmac(payload, secret);
  if (firma !== firmaEsperada) return null;        // firma inválida
  const [email, expStr] = payload.split("|");
  if (Date.now() > Number(expStr)) return null;    // expiró
  return email;
}

function getCookie(request, name) {
  const cookie = request.headers.get("Cookie") || "";
  const m = cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
  return m ? decodeURIComponent(m[1]) : null;
}

// ---- HTML de la pantalla de login ----
function loginHTML(error) {
  return `<!doctype html><html lang="es"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Agronasaja — Portal de Producción</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;
    min-height:100vh;display:flex;align-items:center;justify-content:center;
    background:linear-gradient(rgba(10,20,12,.82),rgba(10,20,12,.92)),
      url('https://images.unsplash.com/photo-1500382017468-9049fed747ef?q=80&w=2000') center/cover fixed;
    color:#fff}
  .wrap{text-align:center;max-width:420px;width:100%;padding:24px}
  .logo{width:78px;height:78px;border-radius:18px;background:rgba(0,0,0,.4);
    display:flex;align-items:center;justify-content:center;margin:0 auto 16px;
    border:1px solid rgba(255,255,255,.15);font-size:34px}
  h1{font-size:34px;letter-spacing:3px;font-weight:800}
  .sub{font-size:12px;letter-spacing:5px;opacity:.7;margin-top:2px}
  .lema{font-style:italic;opacity:.7;font-size:13px;margin:14px 0 26px}
  .card{background:rgba(20,28,22,.72);backdrop-filter:blur(8px);border:1px solid rgba(255,255,255,.12);
    border-radius:14px;padding:24px;text-align:left}
  .card h2{font-size:20px;margin-bottom:2px}
  .card .acc{font-size:12px;opacity:.65;margin-bottom:18px}
  label{font-size:11px;letter-spacing:1px;opacity:.75;text-transform:uppercase;display:block;margin:14px 0 5px}
  input{width:100%;padding:12px 14px;border-radius:9px;border:1px solid rgba(255,255,255,.18);
    background:rgba(0,0,0,.35);color:#fff;font-size:15px}
  input:focus{outline:none;border-color:#84cc16;box-shadow:0 0 0 3px rgba(132,204,22,.25)}
  .err{background:rgba(220,38,38,.25);border:1px solid rgba(248,113,113,.5);color:#fecaca;
    padding:9px 12px;border-radius:8px;font-size:13px;margin-top:14px}
  button{width:100%;margin-top:18px;padding:13px;border:none;border-radius:10px;cursor:pointer;
    background:linear-gradient(90deg,#65a30d,#84cc16);color:#fff;font-size:15px;font-weight:700;
    box-shadow:0 4px 16px rgba(132,204,22,.35)}
  button:hover{filter:brightness(1.05)}
  .foot{margin-top:22px;font-size:11px;opacity:.5}
</style></head><body>
  <div class="wrap">
    <div class="logo">🌱</div>
    <h1>AGRONASAJA</h1>
    <div class="sub">PORTAL DE PRODUCCIÓN</div>
    <div class="lema">"Buscando el mejor rendimiento para su campo."</div>
    <form class="card" method="POST" action="/login">
      <h2>Iniciar sesión</h2>
      <div class="acc">Acceso exclusivo personal autorizado</div>
      <label>Correo electrónico</label>
      <input type="email" name="email" autocomplete="username" required autofocus />
      <label>PIN de acceso</label>
      <input type="password" name="pin" autocomplete="current-password" required />
      ${error ? `<div class="err">${error}</div>` : ""}
      <button type="submit">Ingresar</button>
    </form>
    <div class="foot">Agronasaja SRL — Acceso restringido<br/>agronasaja.com.ar</div>
  </div>
</body></html>`;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // Config sensible desde SECRETS de Cloudflare (env)
    const usuarios = env.USUARIOS_JSON ? JSON.parse(env.USUARIOS_JSON) : USUARIOS_FALLBACK;
    const secret = env.SESSION_SECRET || SESSION_SECRET_FALLBACK;

    // ---- POST /login ----
    if (url.pathname === "/login" && request.method === "POST") {
      const form = await request.formData();
      const email = (form.get("email") || "").trim().toLowerCase();
      const pin = (form.get("pin") || "").trim();
      const pinCorrecto = usuarios[email];
      if (pinCorrecto && pin === String(pinCorrecto)) {
        const token = await crearToken(email, secret);
        return new Response(null, {
          status: 302,
          headers: {
            "Location": "/",
            "Set-Cookie": `${COOKIE_NAME}=${encodeURIComponent(token)}; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=${SESSION_HOURS*3600}`,
          },
        });
      }
      return new Response(loginHTML("Usuario o PIN incorrecto."), {
        status: 401, headers: { "Content-Type": "text/html; charset=utf-8" },
      });
    }

    // ---- GET /logout ----
    if (url.pathname === "/logout") {
      return new Response(null, {
        status: 302,
        headers: {
          "Location": "/",
          "Set-Cookie": `${COOKIE_NAME}=; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=0`,
        },
      });
    }

    // ---- Verificar sesión ----
    const token = getCookie(request, COOKIE_NAME);
    const email = await validarToken(token, secret);
    if (!email) {
      return new Response(loginHTML(null), {
        status: 200, headers: { "Content-Type": "text/html; charset=utf-8" },
      });
    }

    // ---- Autenticado: servir el tablero (proxy a GitHub Pages) ----
    // El documento principal lo traemos del Pages; los assets (Chart.js CDN,
    // raw JSON, API GitHub) los carga el browser directo.
    const target = TABLERO_URL.replace(/\/$/, "") + (url.pathname === "/" ? "/" : url.pathname) + url.search;
    const resp = await fetch(target, {
      cf: { cacheTtl: 60, cacheEverything: false },
      headers: { "User-Agent": "agronasaja-worker" },
    });
    // devolver tal cual, agregando un header para indicar usuario logueado
    const headers = new Headers(resp.headers);
    headers.set("X-Agronasaja-User", email);
    headers.delete("Content-Security-Policy"); // por si Pages mete CSP que rompa
    return new Response(resp.body, { status: resp.status, headers });
  },
};
