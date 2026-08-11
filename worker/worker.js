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

// ---- cache del token de Finnegans (in-memory, valido por isolate) ----
let _fnnToken = null;
let _fnnTokenExp = 0;
async function getFinnegansToken(env) {
  if (_fnnToken && Date.now() < _fnnTokenExp) return _fnnToken;
  const u = `https://api.finneg.com/api/oauth/token?grant_type=client_credentials&client_id=${env.FINNEGANS_CLIENT_ID}&client_secret=${env.FINNEGANS_CLIENT_SECRET}`;
  const r = await fetch(u);
  if (!r.ok) throw new Error("Finnegans auth HTTP " + r.status);
  const token = (await r.text()).trim();
  _fnnToken = token;
  _fnnTokenExp = Date.now() + 50 * 60 * 1000;  // 50 min
  return token;
}

// ---- cache del token de Balanza (api.agronasaja.com), in-memory por isolate ----
let _balToken = null;
let _balTokenExp = 0;
async function getBalanzaToken(env) {
  if (_balToken && Date.now() < _balTokenExp) return _balToken;
  const r = await fetch("https://api.agronasaja.com/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: env.BALANZA_USER, password: env.BALANZA_PASS }),
  });
  if (!r.ok) throw new Error("Balanza auth HTTP " + r.status);
  const j = await r.json();
  if (!j.token) throw new Error("Balanza login sin token");
  _balToken = j.token;
  _balTokenExp = Date.now() + 30 * 60 * 1000;  // 30 min
  return _balToken;
}

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
<title>Agronasaja — Portal de Granos</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;
    min-height:100vh;display:flex;align-items:center;justify-content:center;
    background:linear-gradient(rgba(8,15,30,.78),rgba(8,15,30,.92)),
      url('https://images.unsplash.com/photo-1694105073180-9e7dc1a83fbe?q=80&w=2000') center/cover fixed;
    color:#fff}
  .wrap{text-align:center;max-width:420px;width:100%;padding:24px}
  .logo{width:78px;height:78px;border-radius:18px;background:linear-gradient(135deg,#1e3a8a,#3b82f6);
    display:flex;align-items:center;justify-content:center;margin:0 auto 16px;
    border:1px solid rgba(255,255,255,.18);box-shadow:0 6px 18px rgba(30,58,138,.4)}
  h1{font-size:34px;letter-spacing:3px;font-weight:800}
  .sub{font-size:12px;letter-spacing:5px;opacity:.7;margin-top:2px}
  .lema{font-style:italic;opacity:.7;font-size:13px;margin:14px 0 26px}
  .card{background:rgba(15,23,42,.72);backdrop-filter:blur(8px);border:1px solid rgba(255,255,255,.12);
    border-radius:14px;padding:24px;text-align:left}
  .card h2{font-size:20px;margin-bottom:2px}
  .card .acc{font-size:12px;opacity:.65;margin-bottom:18px}
  label{font-size:11px;letter-spacing:1px;opacity:.75;text-transform:uppercase;display:block;margin:14px 0 5px}
  input{width:100%;padding:12px 14px;border-radius:9px;border:1px solid rgba(255,255,255,.18);
    background:rgba(0,0,0,.35);color:#fff;font-size:15px}
  input:focus{outline:none;border-color:#3b82f6;box-shadow:0 0 0 3px rgba(59,130,246,.3)}
  .err{background:rgba(220,38,38,.25);border:1px solid rgba(248,113,113,.5);color:#fecaca;
    padding:9px 12px;border-radius:8px;font-size:13px;margin-top:14px}
  button{width:100%;margin-top:18px;padding:13px;border:none;border-radius:10px;cursor:pointer;
    background:linear-gradient(90deg,#1e3a8a,#3b82f6);color:#fff;font-size:15px;font-weight:700;
    box-shadow:0 4px 16px rgba(30,58,138,.45)}
  button:hover{filter:brightness(1.05)}
  .foot{margin-top:22px;font-size:11px;opacity:.5}
</style></head><body>
  <div class="wrap">
    <div class="logo"><svg width="46" height="46" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg" aria-label="Grano de soja"><defs><radialGradient id="soy" cx="38%" cy="34%" r="72%"><stop offset="0%" stop-color="#fbf4d8"/><stop offset="55%" stop-color="#ead9a0"/><stop offset="100%" stop-color="#cbb072"/></radialGradient></defs><ellipse cx="50" cy="50" rx="33" ry="38" fill="url(#soy)" transform="rotate(-16 50 50)"/><ellipse cx="33" cy="52" rx="3.4" ry="9" fill="#7a5a2c" transform="rotate(-16 50 50)"/></svg></div>
    <h1>AGRONASAJA</h1>
    <div class="sub">PORTAL DE GRANOS</div>
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
        const h = new Headers({ "Location": "/" });
        h.append("Set-Cookie", `${COOKIE_NAME}=${encodeURIComponent(token)}; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=${SESSION_HOURS*3600}`);
        // Cookie LEGIBLE desde JS (no es de auth, solo para que la UI sepa quien esta logueado y muestre la pestania Personal correspondiente)
        h.append("Set-Cookie", `agronasaja_user=${encodeURIComponent(email)}; Secure; SameSite=Lax; Path=/; Max-Age=${SESSION_HOURS*3600}`);
        return new Response(null, { status: 302, headers: h });
      }
      return new Response(loginHTML("Usuario o PIN incorrecto."), {
        status: 401, headers: { "Content-Type": "text/html; charset=utf-8" },
      });
    }

    // ---- GET /logout ----
    if (url.pathname === "/logout") {
      const h = new Headers({ "Location": "/" });
      h.append("Set-Cookie", `${COOKIE_NAME}=; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=0`);
      h.append("Set-Cookie", `agronasaja_user=; Secure; SameSite=Lax; Path=/; Max-Age=0`);
      return new Response(null, { status: 302, headers: h });
    }

    // ---- EMBEBIDO EN LA EXTRANET: CORS para la página publicada (GitHub Pages) ----
    // El tablero embebido como vista de la extranet se sirve desde ehussenn.github.io
    // y llama a este Worker para el estado compartido (/api/data, whoami, balanza).
    // La identidad viene en el header X-Tablero-User (el email que la extranet le
    // pasó a la vista embebida) y solo se acepta si ese email existe en USUARIOS —
    // mismo nivel de confianza que el ?user= de las vistas embebidas del extranet.
    const EMBED_ORIGIN = "https://ehussenn.github.io";
    const _origin = request.headers.get("Origin") || "";
    const cors = _origin === EMBED_ORIGIN ? {
      "Access-Control-Allow-Origin": EMBED_ORIGIN,
      "Access-Control-Allow-Methods": "GET, POST, PUT, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type, X-Tablero-User",
      "Vary": "Origin",
    } : {};
    if (request.method === "OPTIONS" && url.pathname.startsWith("/api/")) {
      return new Response(null, { status: 204, headers: cors });
    }

    // ---- Verificar sesión ----
    const token = getCookie(request, COOKIE_NAME);
    let email = await validarToken(token, secret);
    if (!email && _origin === EMBED_ORIGIN) {
      const u = (request.headers.get("X-Tablero-User") || "").trim().toLowerCase();
      const usuariosEmb = env.USUARIOS_JSON ? JSON.parse(env.USUARIOS_JSON) : USUARIOS_FALLBACK;
      if (u && usuariosEmb[u]) email = u;   // identidad embebida validada contra la lista
    }
    if (!email) {
      // API endpoints require auth → 401 JSON, no HTML
      if (url.pathname.startsWith("/api/")) {
        return new Response(JSON.stringify({ error: "unauthenticated" }), {
          status: 401, headers: { "Content-Type": "application/json", ...cors },
        });
      }
      return new Response(loginHTML(null), {
        status: 200, headers: { "Content-Type": "text/html; charset=utf-8" },
      });
    }

    // ---- API: persistencia compartida via Cloudflare KV ----
    // GET  /api/data/<key>          → devuelve el JSON guardado bajo esa key (o "[]" si no hay)
    // POST /api/data/<key>          → reemplaza el JSON guardado bajo esa key con el body recibido
    //
    // Keys "compartidas" (todos los usuarios internos ven lo mismo):
    //   - pagos                     → Proyectado Pagos Granos
    // Keys "por usuario" (cada usuario tiene la suya, namespaced por email):
    //   - bandeja_ehussen           → Mi Bandeja personal (solo el owner edita)
    //
    // El listado de keys SHARED esta hardcodeado; cualquier otra key se considera per-user.
    if (url.pathname.startsWith("/api/data/")) {
      const rawKey = url.pathname.slice("/api/data/".length);
      if (!/^[a-z0-9_-]{1,64}$/i.test(rawKey)) {
        return new Response(JSON.stringify({ error: "bad key" }), {
          status: 400, headers: { "Content-Type": "application/json" },
        });
      }
      // Si la KV no esta bindeada al worker (env.TABLERO_KV undefined), devolver 503
      if (!env.TABLERO_KV) {
        return new Response(JSON.stringify({ error: "KV no configurada" }), {
          status: 503, headers: { "Content-Type": "application/json" },
        });
      }
      // SHARED: todos los usuarios internos ven la misma data (no namespaced por email)
      //  - pagos / contratos: Proyectado Pagos y Contratos
      //  - finales_estado: estado de Finales Pendientes (enviadas 🟡 / hechas 🟢) — TODOS ven lo mismo
      const SHARED_KEYS = new Set(["pagos", "contratos", "finales_estado"]);
      const fullKey = SHARED_KEYS.has(rawKey) ? rawKey : `${rawKey}:${email}`;

      if (request.method === "GET") {
        let data = await env.TABLERO_KV.get(fullKey);
        // Migración one-shot: finales_estado antes se guardaba POR USUARIO
        // (finales_estado:<email>), por eso cada compu veía algo distinto. Ahora es
        // compartida. Si la compartida está vacía, fusionar las copias viejas por-usuario.
        if (rawKey === "finales_estado" && (!data || data === "[]")) {
          try {
            const list = await env.TABLERO_KV.list({ prefix: "finales_estado:" });
            const env2 = new Set(), hec2 = new Set();
            for (const k of list.keys) {
              const v = await env.TABLERO_KV.get(k.name);
              if (!v) continue;
              try {
                const o = JSON.parse(v);
                (o.enviadas || []).forEach(x => env2.add(x));
                (o.hechas   || []).forEach(x => hec2.add(x));
              } catch {}
            }
            if (env2.size || hec2.size) {
              data = JSON.stringify({ enviadas: [...env2], hechas: [...hec2] });
              await env.TABLERO_KV.put(fullKey, data, { metadata: { migrated: true, ts: Date.now() } });
            }
          } catch {}
        }
        return new Response(data || "[]", {
          status: 200,
          headers: {
            "Content-Type": "application/json",
            "Cache-Control": "no-store",
            "X-Agronasaja-Key": fullKey,
            ...cors,
          },
        });
      }

      if (request.method === "POST" || request.method === "PUT") {
        const body = await request.text();
        if (body.length > 2_000_000) {
          return new Response(JSON.stringify({ error: "demasiado grande" }), {
            status: 413, headers: { "Content-Type": "application/json" },
          });
        }
        try { JSON.parse(body); }
        catch {
          return new Response(JSON.stringify({ error: "JSON invalido" }), {
            status: 400, headers: { "Content-Type": "application/json" },
          });
        }
        // Guardar con metadata (quien lo modifico y cuando)
        await env.TABLERO_KV.put(fullKey, body, {
          metadata: { user: email, ts: Date.now() },
        });
        return new Response(JSON.stringify({ ok: true, key: fullKey, savedBy: email }), {
          status: 200, headers: { "Content-Type": "application/json", ...cors },
        });
      }

      return new Response(JSON.stringify({ error: "metodo no soportado" }), {
        status: 405, headers: { "Content-Type": "application/json" },
      });
    }

    // ---- /api/whoami: el cliente pregunta quien esta logueado (para UI) ----
    if (url.pathname === "/api/whoami") {
      return new Response(JSON.stringify({ email }), {
        status: 200, headers: { "Content-Type": "application/json", ...cors },
      });
    }

    // ---- /api/balanza/liquidacion?search=<contrato|ctg|cartaporte> ----
    // Proxy autenticado a la balanza (api.agronasaja.com) para "Finales de Compra".
    // Credenciales en secrets BALANZA_USER + BALANZA_PASS. Token cacheado 30 min.
    if (url.pathname === "/api/balanza/liquidacion") {
      if (!env.BALANZA_USER || !env.BALANZA_PASS) {
        return new Response(JSON.stringify({ error: "balanza no configurada (faltan BALANZA_USER/BALANZA_PASS)" }), {
          status: 503, headers: { "Content-Type": "application/json" } });
      }
      const search = (url.searchParams.get("search") || "").trim();
      if (!search) {
        return new Response(JSON.stringify({ error: "falta parametro search" }), {
          status: 400, headers: { "Content-Type": "application/json" } });
      }
      try {
        const callBalanza = (t) => fetch(
          `https://api.agronasaja.com/api/liquidacionescompras/page?page=1&pageSize=50&search=${encodeURIComponent(search)}`,
          { headers: { "Authorization": "Bearer " + t } });
        let tok = await getBalanzaToken(env);
        let r = await callBalanza(tok);
        if (r.status === 401) { _balToken = null; tok = await getBalanzaToken(env); r = await callBalanza(tok); }
        const body = await r.text();
        return new Response(body, {
          status: r.status,
          headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });
      } catch (e) {
        return new Response(JSON.stringify({ error: "balanza error: " + String(e) }), {
          status: 502, headers: { "Content-Type": "application/json" } });
      }
    }

    // ---- /api/finnegans/ctg/<CTG>: proxy autenticado al detalle de un CTG en Finnegans ----
    // Trae la cadena cartaPortePorCTG (3 filas: recepcion compra, traslado CV, traslado venta).
    // Usa los Cloudflare secrets FINNEGANS_CLIENT_ID + FINNEGANS_CLIENT_SECRET.
    // Cachea el token Finnegans por 50 min en memoria global del Worker.
    if (url.pathname.startsWith("/api/finnegans/ctg/")) {
      const ctg = url.pathname.slice("/api/finnegans/ctg/".length);
      if (!/^\d{6,15}$/.test(ctg)) {
        return new Response(JSON.stringify({ error: "CTG invalido" }), {
          status: 400, headers: { "Content-Type": "application/json" },
        });
      }
      if (!env.FINNEGANS_CLIENT_ID || !env.FINNEGANS_CLIENT_SECRET) {
        return new Response(JSON.stringify({ error: "Falta configurar FINNEGANS_CLIENT_ID/SECRET en el Worker" }), {
          status: 503, headers: { "Content-Type": "application/json" },
        });
      }
      try {
        const tok = await getFinnegansToken(env);
        const r = await fetch(`https://api.finneg.com/api/reports/cartaPortePorCTG?CTG=${encodeURIComponent(ctg)}`, {
          headers: { "Authorization": `Bearer ${tok}` },
        });
        const txt = await r.text();
        if (!r.ok) {
          return new Response(JSON.stringify({ error: `Finnegans HTTP ${r.status}`, body: txt.slice(0, 300) }), {
            status: 502, headers: { "Content-Type": "application/json" },
          });
        }
        let arr;
        try { arr = JSON.parse(txt); } catch { arr = []; }
        return new Response(JSON.stringify({ ctg, cartaPorte: arr }), {
          status: 200,
          headers: {
            "Content-Type": "application/json",
            "Cache-Control": "private, max-age=300",
          },
        });
      } catch (e) {
        return new Response(JSON.stringify({ error: String(e).slice(0, 200) }), {
          status: 500, headers: { "Content-Type": "application/json" },
        });
      }
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
    // No cachear contenido autenticado en el navegador: así al cerrar sesión
    // (o sin cookie) el browser vuelve a pedirle al Worker y muestra el login.
    headers.set("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0");
    headers.set("Pragma", "no-cache");
    return new Response(resp.body, { status: resp.status, headers });
  },
};
