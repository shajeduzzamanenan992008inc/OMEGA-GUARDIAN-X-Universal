"""Browser E2E verification of the control-plane dashboard (UI-04/UI-05/UI-06).

Starts a real API server on loopback with a temporary SQLite store, creates an
organization, project, governance record, audit trail, host and scoped service
token, then drives the dashboard in a real Chromium browser with Playwright.

The token is generated in memory, typed into the form, and never written to the
evidence file. The script fails on console errors, uncaught page errors, failed
requests or any missing view.

Usage:

    python _dev/tools/verify_dashboard_e2e.py \
        --evidence docs/evidence/dashboard-e2e-2026-11-28.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

STATE = Path(tempfile.mkdtemp(prefix="ogx-dashboard-e2e-"))
os.environ["OGX_HOME"] = str(STATE)
os.environ.pop("OGX_DATABASE_URL", None)

from ogx.api.server import ApiContext, create_server  # noqa: E402
from ogx.enterprise.authz import PolicyEngine  # noqa: E402
from ogx.enterprise.service_identity import ServiceIdentityManager  # noqa: E402
from ogx.enterprise.store import EnterpriseStore  # noqa: E402
from ogx.hosts.registry import HostRegistry  # noqa: E402

SCOPES = [
    "organization.read", "member.read", "project.read", "audit.read",
    "audit.export", "governance.read", "policy.read", "host.read", "job.read",
    "dashboard.view",
]


def _evidence_path(value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", default=None)
    parser.add_argument("--screenshot-dir", default="docs/evidence")
    parser.add_argument("--browser", default="chromium",
                        choices=["chromium", "firefox", "webkit"])
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args(argv)

    try:
        from playwright.sync_api import expect, sync_playwright
    except ImportError:
        print("playwright is not installed; pip install playwright && playwright install")
        return 2

    result: dict[str, object] = {
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python": sys.version.split()[0],
        "browser": args.browser,
        "checks": {},
        "console_errors": [],
        "page_errors": [],
        "failed_requests": [],
        "ok": False,
    }
    started = time.monotonic()
    store = EnterpriseStore(STATE / "enterprise.sqlite3")
    store.initialize()
    policy = PolicyEngine()
    org = store.create_organization("e2e-org", "E2E Organization", "owner@example.com")
    organization_id = org["organization_id"]
    store.register_project(organization_id, "dashboard-project", "local", None,
                           "owner@example.com")
    store.create_governance_record(
        organization_id, "risk", "Quarterly access review", "owner@example.com",
        "high", "open", "owner@example.com",
    )
    manager = ServiceIdentityManager(store, policy)
    account = manager.create_account(
        organization_id, "dashboard-e2e", "admin", SCOPES, "owner@example.com"
    )
    issued = manager.issue_token(organization_id, account["account_id"],
                                 "owner@example.com", ttl_seconds=900)
    token = issued.token
    host_id = uuid.uuid4()
    registry = HostRegistry()
    registry.register_host(str(host_id), "local_filesystem", "local", "E2E filesystem",
                           str(ROOT))
    context = ApiContext(store=store, policy=policy, rate_limit=1000)
    server = create_server(context, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    result["base_url"] = base
    screenshot_dir = _evidence_path(args.screenshot_dir) or (ROOT / "docs" / "evidence")
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    screenshots: list[str] = []

    try:
        with sync_playwright() as playwright:
            browser_type = getattr(playwright, args.browser)
            try:
                browser = browser_type.launch(headless=not args.headed)
            except Exception as exc:  # noqa: BLE001 - browser availability is environmental
                print(f"{args.browser} could not launch: {type(exc).__name__}: {exc}")
                return 2
            result["browser_version"] = browser.version
            page = browser.new_page(viewport={"width": 1280, "height": 900})

            def on_console(message) -> None:
                if message.type != "error":
                    return
                if "favicon" in message.text:
                    return
                if "401" in message.text or "Unauthorized" in message.text:
                    # The invalid-token scenario intentionally produces a 401.
                    return
                result["console_errors"].append(message.text)

            def on_page_error(error) -> None:
                result["page_errors"].append(str(error))

            def on_request_failed(request) -> None:
                if "favicon" not in request.url:
                    result["failed_requests"].append(
                        f"{request.method} {request.url}: {request.failure}"
                    )

            def on_response(response) -> None:
                if "/api/" in response.url:
                    result.setdefault("api_calls", []).append(
                        f"{response.status} {response.request.method} {response.url}"
                    )
                if response.status == 401:
                    # The invalid-token scenario intentionally expects a 401.
                    return
                if response.status >= 400 and "favicon" not in response.url:
                    result["failed_requests"].append(
                        f"HTTP {response.status} {response.url}"
                    )

            page.on("console", on_console)
            page.on("pageerror", on_page_error)
            page.on("requestfailed", on_request_failed)
            page.on("response", on_response)

            response = page.goto(base + "/", wait_until="load")
            assert response is not None
            headers = response.headers
            result["checks"]["page"] = {
                "status": response.status,
                "csp": "default-src 'none'" in headers.get("content-security-policy", ""),
                "frame_options": headers.get("x-frame-options") == "DENY",
                "no_referrer": headers.get("referrer-policy") == "no-referrer",
                "title": page.title(),
                "headers": dict(headers),
            }
            assert response.status == 200
            assert page.title() == "OGX Control Plane"

            page.fill("#token", token)
            page.click("#token-form button[type=submit]")
            try:
                page.wait_for_selector("#overview-cards .card", timeout=15_000)
            except Exception:
                result["debug"] = {
                    "status": page.locator("#status").inner_text(),
                    "api_calls": result.get("api_calls", []),
                    "html": page.locator("main").inner_text()[:2000],
                }
                page.screenshot(path=str(screenshot_dir / "dashboard-failure.png"),
                                full_page=True)
                raise
            expect(page.locator("#overview-cards .card")).to_have_count(4, timeout=15_000)
            overview = page.locator("#overview-cards .card .value").all_text_contents()
            result["checks"]["overview"] = {
                "cards": overview,
                "product_line": page.locator("#product-line").inner_text(),
                "status": page.locator("#status").inner_text(),
            }
            assert overview[0] == "1", overview
            assert overview[1] == "1", overview
            assert overview[2] == "1", overview
            assert "e2e-org" in result["checks"]["overview"]["product_line"]
            assert token not in page.content()
            overview_shot = screenshot_dir / "dashboard-overview-2026-11-28.png"
            page.screenshot(path=str(overview_shot), full_page=True)
            screenshots.append(str(overview_shot.relative_to(ROOT)))

            page.click('[role="tab"][data-view="projects"]')
            page.wait_for_selector("#projects table")
            projects_text = page.locator("#projects").inner_text()
            result["checks"]["projects"] = {"found": "dashboard-project" in projects_text}
            assert "dashboard-project" in projects_text
            page.screenshot(path=str(screenshot_dir / "dashboard-projects-2026-11-28.png"),
                            full_page=True)
            screenshots.append(str((screenshot_dir / "dashboard-projects-2026-11-28.png")
                                   .relative_to(ROOT)))

            page.click('[role="tab"][data-view="governance"]')
            page.wait_for_selector("#governance table")
            governance_text = page.locator("#governance").inner_text()
            result["checks"]["governance"] = {
                "found": "Quarterly access review" in governance_text
            }
            assert "Quarterly access review" in governance_text

            page.click('[role="tab"][data-view="audit"]')
            page.wait_for_selector("#audit table")
            audit_text = page.locator("#audit").inner_text()
            result["checks"]["audit"] = {
                "found": "project.register" in audit_text,
                "rows": page.locator("#audit tbody tr").count(),
            }
            assert "project.register" in audit_text

            page.click('[role="tab"][data-view="hosts"]')
            page.wait_for_selector("#hosts table")
            hosts_text = page.locator("#hosts").inner_text()
            result["checks"]["hosts"] = {
                "found": "local_filesystem" in hosts_text
            }
            assert "local_filesystem" in hosts_text

            page.click('[role="tab"][data-view="policy"]')
            page.wait_for_selector("#policy table")
            policy_text = page.locator("#policy").inner_text()
            result["checks"]["policy"] = {"found": "project.read" in policy_text}
            assert "project.read" in policy_text

            page.click("#disconnect")
            expect(page.locator("#status")).to_contain_text("Signed out", timeout=15_000)
            result["checks"]["disconnect"] = {
                "status": page.locator("#status").inner_text(),
                "overview_cleared": page.locator("#overview-cards").inner_text() == "",
            }
            assert result["checks"]["disconnect"]["overview_cleared"]

            page.fill("#token", "ogxst.invalid-token")
            page.click("#token-form button[type=submit]")
            expect(page.locator("#status")).to_contain_text("rejected", timeout=15_000)
            result["checks"]["invalid_token"] = {
                "status": page.locator("#status").inner_text()
            }
            assert "rejected" in result["checks"]["invalid_token"]["status"]

            # Reconnect once to prove recovery after a rejected token.
            page.fill("#token", token)
            page.click("#token-form button[type=submit]")
            page.wait_for_selector("#overview-cards .card")
            result["checks"]["reconnect"] = {
                "cards": page.locator("#overview-cards .card .value").all_text_contents()
            }
            assert result["checks"]["reconnect"]["cards"][0] == "1"

            browser.close()
        result["screenshots"] = screenshots
        result["ok"] = (
            not result["console_errors"]
            and not result["page_errors"]
            and not result["failed_requests"]
            and all(
                value.get("found", True) if isinstance(value, dict) else True
                for value in result["checks"].values()
            )
        )
    except Exception as exc:  # noqa: BLE001 - evidence tool reports the class
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        result["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        store.close()
        shutil.rmtree(STATE, ignore_errors=True)

    serialized = json.dumps(result, indent=2, sort_keys=True)
    if token in serialized:
        print("refusing to print evidence: the service token appeared in the payload")
        return 1
    print(serialized)
    evidence = _evidence_path(args.evidence)
    if evidence:
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(serialized + "\n", encoding="utf-8")
        print(f"evidence written to {evidence}")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
