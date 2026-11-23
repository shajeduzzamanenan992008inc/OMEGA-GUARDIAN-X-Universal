"""Runnable demo: a plain WSGI app protected by the shield (stdlib only).

    python omega-shield/guard/runtime/examples/wsgi_app.py
    curl "http://127.0.0.1:8099/hello"                       -> 200 (with security headers)
    curl "http://127.0.0.1:8099/s?q=%3Cscript%3E"            -> 403

Embedding pattern for Flask/Django/Bottle: wrap the WSGI callable, e.g. `app.wsgi_app = shield.ShieldMiddleware(app.wsgi_app)`.
"""
import runpy
from pathlib import Path
from wsgiref.simple_server import make_server

INIT = Path(__file__).resolve().parents[3] / "init.py"
shield = runpy.run_path(str(INIT))["load_runtime"]()      # imports ogx.runtime without leaving sys.path modified


def app(environ, start_response):
    start_response("200 OK", [("Content-Type", "text/plain; charset=utf-8")])
    return [f"hello from the protected app (request id {environ.get('omega.request_id')})\n".encode()]


if __name__ == "__main__":
    shield.observe.configure(console=True)                  # JSON log file + console, outside the project
    protected = shield.ShieldMiddleware(app)
    with make_server("127.0.0.1", 8099, protected) as server:
        print("listening on http://127.0.0.1:8099  (Ctrl+C to stop)")
        server.serve_forever()
