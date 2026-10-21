#!/usr/bin/env python3
"""higgsfield_espera.py — espera generaciones de Higgsfield sin gastar tokens de Claude.

Habla directo con el conector del plan (mcp.higgsfield.ai, créditos de la web; no la
API prepagada) y llama `jobs_wait` en un ciclo hasta que todo termina. Esperar no
gasta créditos.

Una vez por Mac (abre Higgsfield en el navegador; se pica "Permitir"):
  python3 higgsfield_espera.py login [--correo CUENTA@gmail.com]
Cada vez (con run_in_background, para que la app avise al terminar):
  python3 higgsfield_espera.py esperar JOB_ID [JOB_ID ...] [--max 1800] [--voz "Ya quedó"]
Salida: una línea por job "index  estado  url-o-error" y al final LISTO / NO TERMINÓ.
Sale 0 si todos terminaron bien, 1 si alguno falló o se acabó el tiempo, 2 si falta login.

La sesión (client_id + refresh token) vive en el Llavero, servicio higgsfield-espera.
Nunca se imprime. Prueba: test_higgsfield_espera.py
"""
import argparse
import base64
import hashlib
import http.server
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

MCP = os.environ.get("HF_ESPERA_MCP", "https://mcp.higgsfield.ai/mcp")
AUTH = os.environ.get("HF_ESPERA_AUTH", "https://clerk.higgsfield.ai")
LLAVERO = os.environ.get("HF_ESPERA_LLAVERO", "higgsfield-espera")
UA = "higgsfield-espera/1.0"
PUERTO = 33418
LOCAL_STATE = os.path.expanduser("~/Library/Application Support/Google/Chrome/Local State")


# ---------- Llavero ----------
def guardar_sesion(d):
    hexa = json.dumps(d).encode().hex()  # por stdin y en hex: no pasa por la línea de comandos
    cmd = f'add-generic-password -U -a "{os.environ.get("USER", "")}" -s "{LLAVERO}" -X {hexa}\n'
    r = subprocess.run(["security", "-i"], input=cmd, text=True, capture_output=True)
    if r.returncode != 0:  # 36 = Llavero bloqueado (pasa al entrar por SSH, no frente a la Mac)
        sys.exit(f"NO: no pude guardar en el Llavero (código {r.returncode}"
                 + ("; está bloqueado: corre esto desde la Mac, no por SSH" if r.returncode == 36 else "") + ").")


def leer_sesion():
    r = subprocess.run(["security", "find-generic-password", "-a", os.environ.get("USER", ""),
                        "-s", LLAVERO, "-w"], capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return json.loads(r.stdout.strip())


# ---------- HTTP ----------
def pedir(url, datos=None, form=False, headers=None, timeout=60):
    h = {"User-Agent": UA, **(headers or {})}
    cuerpo = None
    if datos is not None:
        if form:
            cuerpo = urllib.parse.urlencode(datos).encode()
            h["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            cuerpo = json.dumps(datos).encode()
            h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=cuerpo, headers=h, method="POST" if cuerpo else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, dict(r.headers), r.read().decode("utf-8", "replace")


def metadatos():
    return json.loads(pedir(AUTH + "/.well-known/oauth-authorization-server")[2])


# ---------- Login (registro dinámico + PKCE) ----------
def abrir(url, correo):
    if correo and os.path.exists(LOCAL_STATE):
        try:
            perfiles = json.load(open(LOCAL_STATE))["profile"]["info_cache"]
            carpeta = next(k for k, v in perfiles.items() if v.get("user_name", "").lower() == correo.lower())
            subprocess.run(["open", "-na", "Google Chrome", "--args", f"--profile-directory={carpeta}", url])
            return
        except (StopIteration, KeyError, ValueError):
            print(f"(no encontré un Chrome con {correo}; abro el navegador por defecto)")
    subprocess.run(["open", url])


def directo_al_permiso(url):
    """Clerk manda a higgsfield.ai/auth/sign-in?redirect_url=<página de permiso>; con la
    sesión ya abierta, ese sign-in te avienta a tu perfil y el permiso nunca sale
    (20-oct-2026). Se siguen los saltos aquí y se abre directo la página de permiso."""
    class SinSaltos(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a):
            return None
    abridor = urllib.request.build_opener(SinSaltos)
    for _ in range(5):
        try:
            abridor.open(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20)
            return url
        except urllib.error.HTTPError as e:
            sig = e.headers.get("Location")
            if e.code not in (301, 302, 303, 307, 308) or not sig:
                return url
            sig = urllib.parse.urljoin(url, sig)
            destino = urllib.parse.parse_qs(urllib.parse.urlparse(sig).query).get("redirect_url")
            if "/sign-in" in sig and destino:
                return destino[0]
            url = sig
        except OSError:
            return url
    return url


def login(correo):
    m = metadatos()
    redirect = f"http://127.0.0.1:{PUERTO}/callback"
    reg = json.loads(pedir(m["registration_endpoint"], {
        "client_name": "higgsfield-espera", "redirect_uris": [redirect],
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
        "token_endpoint_auth_method": "none", "scope": "openid email offline_access"})[2])
    client_id = reg["client_id"]
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    estado = secrets.token_urlsafe(16)
    url = m["authorization_endpoint"] + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect,
        "scope": "openid email offline_access", "state": estado, "code_challenge": challenge,
        "code_challenge_method": "S256", "resource": MCP, "prompt": "consent"})
    recibido = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            recibido.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            ok = "code" in q and q.get("state", [""])[0] == estado
            self.wfile.write(("<h2>Listo, ya puedes cerrar esta pestaña.</h2>" if ok else
                              "<h2>No se pudo: regresa a Claude.</h2>").encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", PUERTO), H)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    abrir(directo_al_permiso(url), correo)
    print("Abrí Higgsfield en el navegador: pica Permitir (tienes 5 minutos).", flush=True)
    t0 = time.time()
    while not recibido and time.time() - t0 < 300:
        time.sleep(0.5)
    srv.server_close()
    if recibido.get("state") != estado or "code" not in recibido:
        sys.exit(f"NO: no llegó el permiso ({recibido.get('error', 'se acabó el tiempo')}).")
    tok = json.loads(pedir(m["token_endpoint"], {
        "grant_type": "authorization_code", "code": recibido["code"], "redirect_uri": redirect,
        "client_id": client_id, "code_verifier": verifier, "resource": MCP}, form=True)[2])
    if "refresh_token" not in tok:
        sys.exit("NO: Higgsfield no dio refresh token; no guardé nada.")
    guardar_sesion({"client_id": client_id, "refresh_token": tok["refresh_token"],
                    "token_endpoint": m["token_endpoint"]})
    print("LISTO: sesión guardada en el Llavero.")


def token_vigente():
    s = leer_sesion()
    if not s:
        print("FALTA LOGIN: corre `higgsfield_espera.py login` una vez.")
        sys.exit(2)
    try:
        tok = json.loads(pedir(s["token_endpoint"], {"grant_type": "refresh_token",
                         "refresh_token": s["refresh_token"], "client_id": s["client_id"],
                         "resource": MCP}, form=True)[2])
    except urllib.error.HTTPError as e:
        print(f"FALTA LOGIN: la sesión ya no sirve (HTTP {e.code}); corre `login` otra vez.")
        sys.exit(2)
    if tok.get("refresh_token") and tok["refresh_token"] != s["refresh_token"]:
        s["refresh_token"] = tok["refresh_token"]  # rota: se guarda el nuevo
        guardar_sesion(s)
    return tok["access_token"]


# ---------- MCP (HTTP transmitible, sin dependencias) ----------
class Mcp:
    def __init__(self, token):
        self.h = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"}
        self.n = 0
        r = self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "higgsfield-espera", "version": "1.0"}})
        if "error" in r:
            raise RuntimeError(f"initialize: {r['error']}")
        self.rpc("notifications/initialized", None, notificacion=True)

    def rpc(self, metodo, params, notificacion=False):
        msg = {"jsonrpc": "2.0", "method": metodo}
        if params is not None:
            msg["params"] = params
        if not notificacion:
            self.n += 1
            msg["id"] = self.n
        _, hdr, txt = pedir(MCP, msg, headers=self.h, timeout=90)
        sid = {k.lower(): v for k, v in hdr.items()}.get("mcp-session-id")
        if sid:
            self.h["Mcp-Session-Id"] = sid
        if notificacion or not txt.strip():
            return {}
        if txt.lstrip().startswith("{"):
            return json.loads(txt)
        for linea in txt.splitlines():  # respuesta SSE: la última línea data: con nuestro id
            if linea.startswith("data:"):
                d = json.loads(linea[5:].strip())
                if d.get("id") == msg.get("id"):
                    return d
        return {}

    def herramienta(self, nombre, args):
        r = self.rpc("tools/call", {"name": nombre, "arguments": args})
        if "error" in r:
            raise RuntimeError(f"{nombre}: {r['error']}")
        res = r.get("result", {})
        if res.get("structuredContent"):
            return res["structuredContent"]
        texto = "".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")
        return json.loads(texto) if texto.strip().startswith("{") else {"texto": texto}


def esperar(ids, maximo, voz):
    mcp = Mcp(token_vigente())
    pendientes = [{"index": i, "job_id": j} for i, j in enumerate(ids)]
    final, t0, vueltas = {}, time.time(), 0
    while pendientes and time.time() - t0 < maximo:
        vueltas += 1
        siguientes = []
        for g in range(0, len(pendientes), 12):
            r = mcp.herramienta("jobs_wait", {"jobs": pendientes[g:g + 12], "timeout_seconds": 15})
            for j in r.get("jobs", []):
                st = j.get("status", "?")
                if st in ("queued", "pending", "in_progress", "processing", "running"):
                    siguientes.append({"index": j["index"], "job_id": j["job_id"]})
                else:
                    final[j["index"]] = j
        pendientes = siguientes
        if pendientes:
            time.sleep(3)
    bien = True
    for i, j in enumerate(ids):
        d = final.get(i)
        if not d:
            print(f"{i}  sin terminar  {j}")
            bien = False
            continue
        ok = d.get("status") == "completed"
        bien &= ok
        print(f"{i}  {d.get('status')}  {d.get('result_url') or d.get('error') or ''}")
    seg = time.time() - t0
    print(("LISTO" if bien and not pendientes else "NO TERMINÓ") + f" en {seg:.0f} s ({vueltas} vueltas)")
    if voz and bien and not pendientes:
        vz = os.environ.get("HF_ESPERA_VOZ") or os.path.expanduser("~/Desktop/Brain/⚙️ Meta/scripts/voz.sh")
        subprocess.run(["bash", vz, voz] if os.path.exists(vz) else ["say", voz], capture_output=True)
    sys.exit(0 if bien and not pendientes else 1)


def main():
    p = argparse.ArgumentParser(description="Espera generaciones de Higgsfield sin gastar tokens")
    sub = p.add_subparsers(dest="op", required=True)
    l = sub.add_parser("login")
    l.add_argument("--correo")
    e = sub.add_parser("esperar")
    e.add_argument("ids", nargs="+")
    e.add_argument("--max", type=float, default=1800)
    e.add_argument("--voz")
    a = p.parse_args()
    if a.op == "login":
        login(a.correo)
    else:
        esperar(a.ids, a.max, a.voz)


if __name__ == "__main__":
    main()
