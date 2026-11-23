"""Runtime guard (firewall, request validation, rate limiting, access control) and observability.

New in the shield redesign - not part of the original OGX engine. Stdlib only; layer: kernel only.
Submodules are imported eagerly so ``init.load_runtime()`` can drop its temporary sys.path entry afterwards.
"""
from __future__ import annotations

from ogx.runtime import access, asgi, config, engine, observe, proxy, ratelimit, request, rules, wsgi
from ogx.runtime.asgi import ShieldASGI
from ogx.runtime.config import RuntimeConfig, load_runtime_config
from ogx.runtime.engine import Shield
from ogx.runtime.request import Decision, Request
from ogx.runtime.wsgi import ShieldMiddleware

__all__ = ["Decision", "Request", "RuntimeConfig", "Shield", "ShieldASGI", "ShieldMiddleware", "access", "asgi", "config",
           "engine", "load_runtime_config", "observe", "proxy", "ratelimit", "request", "rules", "wsgi"]
