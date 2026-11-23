"""Runnable ASGI demo (needs an ASGI server such as uvicorn, which is NOT part of the shield):

    uvicorn --app-dir omega-shield/guard/runtime/examples asgi_app:app --port 8099

Pattern for FastAPI/Starlette:  app = shield.ShieldASGI(app)   (wrap the outermost app)
"""
import runpy
from pathlib import Path

shield = runpy.run_path(str(Path(__file__).resolve().parents[3] / "init.py"))["load_runtime"]()


async def inner(scope, receive, send):
    if scope["type"] != "http":
        return
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"hello from the protected ASGI app\n"})


app = shield.ShieldASGI(inner)
