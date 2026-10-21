#!/usr/bin/env python3
"""Candado de higgsfield_espera.py con un Higgsfield falso (token + MCP), sin red ni créditos.
Usa un servicio de Llavero de prueba y lo borra al final.
Correr: /opt/homebrew/bin/python3 "⚙️ Meta/skills/higgsfield-espera/test_higgsfield_espera.py" """
import http.server
import json
import os
import subprocess
import sys
import threading
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "higgsfield_espera.py"
LLAVERO = "higgsfield-espera-PRUEBA"
sys.path.insert(0, str(SCRIPT.parent))
os.environ["HF_ESPERA_LLAVERO"] = LLAVERO
import higgsfield_espera as he  # noqa: E402


def servidor(estado):
    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            cuerpo = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
            if self.path == "/token":
                estado["refrescos"] += 1
                if estado.get("token_muerto"):
                    return self._json(400, {"error": "invalid_grant"})
                return self._json(200, {"access_token": "AT", "refresh_token": f"RT{estado['refrescos']}"})
            msg = json.loads(cuerpo)
            assert self.headers.get("Authorization") == "Bearer AT"
            if "id" not in msg:
                return self._json(202, None)
            if msg["method"] == "initialize":
                return self._json(200, {"jsonrpc": "2.0", "id": msg["id"], "result": {}}, sid="S1")
            assert self.headers.get("Mcp-Session-Id") == "S1"
            estado["vueltas"] += 1
            st = "completed" if estado["vueltas"] >= estado["listo_en"] else "in_progress"
            jobs = [{"index": j["index"], "job_id": j["job_id"], "status": st,
                     **({"result_url": "https://x/" + j["job_id"]} if st == "completed" else {})}
                    for j in msg["params"]["arguments"]["jobs"]]
            data = json.dumps({"jsonrpc": "2.0", "id": msg["id"],
                               "result": {"content": [{"type": "text", "text": json.dumps({"jobs": jobs})}]}})
            self.send_response(200)  # como Higgsfield: respuesta en SSE
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(f"event: message\ndata: {data}\n\n".encode())

        def _json(self, cod, obj, sid=None):
            self.send_response(cod)
            self.send_header("Content-Type", "application/json")
            if sid:
                self.send_header("Mcp-Session-Id", sid)
            self.end_headers()
            if obj is not None:
                self.wfile.write(json.dumps(obj).encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class Casos(unittest.TestCase):
    def setUp(self):
        self.estado = {"refrescos": 0, "vueltas": 0, "listo_en": 3}
        self.srv = servidor(self.estado)
        self.base = f"http://127.0.0.1:{self.srv.server_port}"
        he.guardar_sesion({"client_id": "C", "refresh_token": "RT0", "token_endpoint": self.base + "/token"})

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        subprocess.run(["security", "delete-generic-password", "-a", os.environ.get("USER", ""), "-s", LLAVERO],
                       capture_output=True)

    def correr(self, *ids):
        env = dict(os.environ, HF_ESPERA_MCP=self.base + "/mcp", HF_ESPERA_LLAVERO=LLAVERO)
        return subprocess.run([sys.executable, str(SCRIPT), "esperar", *ids, "--max", "60"],
                              capture_output=True, text=True, env=env, timeout=90)

    def test_espera_varias_vueltas_hasta_terminar(self):
        p = self.correr("11111111-1111-4111-8111-111111111111")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("completed  https://x/11111111", p.stdout)
        self.assertIn("LISTO", p.stdout)
        self.assertEqual(self.estado["vueltas"], 3)

    def test_guarda_el_refresh_token_nuevo(self):
        self.correr("11111111-1111-4111-8111-111111111111")
        self.assertEqual(he.leer_sesion()["refresh_token"], "RT1")

    def test_sesion_muerta_pide_login_sin_tronar(self):
        self.estado["token_muerto"] = True
        p = self.correr("11111111-1111-4111-8111-111111111111")
        self.assertEqual(p.returncode, 2)
        self.assertIn("FALTA LOGIN", p.stdout)

    def test_sin_sesion_pide_login(self):
        subprocess.run(["security", "delete-generic-password", "-a", os.environ.get("USER", ""), "-s", LLAVERO],
                       capture_output=True)
        p = self.correr("11111111-1111-4111-8111-111111111111")
        self.assertEqual(p.returncode, 2)

    def test_salto_de_sign_in_se_brinca(self):
        # sin red: un URL que no responde se regresa tal cual
        self.assertEqual(he.directo_al_permiso("http://127.0.0.1:9/x"), "http://127.0.0.1:9/x")


if __name__ == "__main__":
    unittest.main()
