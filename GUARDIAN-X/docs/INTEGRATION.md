# Integration

## 1. Drop in
Copy the `omega-shield/` folder into the **root** of the host project (any language, any size). Requirement: Python 3.11+ on the
developer/CI machine (the host application itself does not need Python unless you embed the guard in it).

```
python omega-shield/init.py status        # Windows: py -3 omega-shield\init.py status
```

## 2. Protect the repository
```
python omega-shield/init.py scan [--strict]          # read-only; exit 1 on high/critical with --strict
python omega-shield/init.py audit                    # health + security + maintenance report
python omega-shield/init.py hooks install            # dry-run
python omega-shield/init.py hooks install --confirm  # .git/hooks/pre-commit  (--hook pre-push also supported)
```
CI: `sh omega-shield/guard/gates/ci-gate.sh` or `python omega-shield/init.py scan --strict`.

## 3. Protect the running application
* Python WSGI/ASGI: `shield = runpy.run_path("omega-shield/init.py")["load_runtime"]()` then `shield.ShieldMiddleware(app)` / `shield.ShieldASGI(app)`.
* Any language: `python omega-shield/init.py proxy --upstream http://127.0.0.1:3000` (experimental).
* Configure in `guard/runtime/runtime.toml`; check with `init.py guard check`; try requests with `init.py guard test "/path?x=1"`.

## 4. Observe
`shield.observe.configure()`, `ErrorTracker`, `init.py logs | errors | snapshot` - see `OBSERVABILITY.md`.

## 5. Connect host tools (opt-in)
```
python omega-shield/init.py link plan                           # shows the groups; changes nothing
python omega-shield/init.py link apply --with copilot,deps --confirm
python omega-shield/init.py link status
python omega-shield/init.py link remove --confirm               # removes only unchanged files
```

## 6. Remove
`init.py hooks remove --confirm`, `init.py link remove --confirm`, delete the folder, optionally delete the state folder shown by `status`.

## Web-root hosts
Block the folder from HTTP (it contains source): nginx `location ^~ /omega-shield/ { deny all; }`; Apache `omega-shield/.htaccess`: `Require all denied`.

## Several projects, one shield
`python omega-shield/scripts/scan_many.py PATH1 PATH2 --strict` (external mode: nothing is written into the projects).
