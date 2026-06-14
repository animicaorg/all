"""
animica.ena.service
===================

Minimal HTTP API for ENA, exposing sessions, indexes, jobs, receipts, eval
runs, and training runs. Implemented on the standard-library ``http.server``
so ``animica ena serve`` needs no extra dependencies; the handler dispatches
to the same service objects the CLI uses.

Routes
------
GET  /health
GET  /jobs              ?status=&type=
GET  /jobs/<id>
POST /jobs              {type, params}
POST /jobs/<id>/run
POST /jobs/<id>/verify
POST /jobs/<id>/receipt
POST /jobs/<id>/export
GET  /indexes
POST /search            {query, mode, index}
GET  /training/runs
GET  /training/runs/<id>
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse


def _make_handler(facade):
    class Handler(BaseHTTPRequestHandler):
        server_version = "AnimicaENA/0.2"

        def log_message(self, *args):  # quiet by default
            pass

        def _send(self, code: int, payload: Any) -> None:
            body = json.dumps(payload, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.send_header("access-control-allow-origin", "*")
            self.send_header("access-control-allow-headers", "content-type")
            self.send_header("access-control-allow-methods", "GET, POST, OPTIONS")
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):  # noqa: N802 - CORS preflight
            self._send(204, {})

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("content-length") or 0)
            if not length:
                return {}
            raw = self.rfile.read(length).decode("utf-8")
            try:
                return json.loads(raw) if raw else {}
            except ValueError:
                return {}

        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            try:
                if path == "/health":
                    return self._send(200, {"status": "ok", "service": "ena"})
                if path == "/jobs":
                    return self._send(200, {"jobs": facade.jobs.list(
                        status=q.get("status"), job_type=q.get("type"))})
                if path.startswith("/jobs/"):
                    return self._send(200, facade.jobs.get(path.split("/", 2)[2]))
                if path == "/indexes":
                    return self._send(200, {"indexes": facade.store.list_indexes()})
                if path == "/training/runs":
                    return self._send(200, {"runs": facade.list_runs()})
                if path.startswith("/training/runs/"):
                    return self._send(200, facade.run_status(path.rsplit("/", 1)[1]))
                if path == "/stats":
                    return self._send(200, facade.stats())
                if path == "/datasets":
                    return self._send(200, {"datasets": facade.list_datasets()})
                if path == "/demand/config":
                    return self._send(200, facade.demand.config())
                if path == "/demand/status":
                    jid = q.get("job_id")
                    if not jid:
                        return self._send(400, {"error": "job_id required"})
                    return self._send(200, facade.demand.status(jid))
                if path == "/wallet/poll":
                    rid = q.get("id") or q.get("requestId")
                    if not rid:
                        return self._send(400, {"error": "id required"})
                    return self._send(200, facade.walletconnect.poll(rid))
                return self._send(404, {"error": "not found", "path": path})
            except Exception as exc:  # noqa: BLE001
                return self._send(400, {"error": str(exc)})

        def do_POST(self):  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            body = self._body()
            try:
                if path == "/jobs":
                    return self._send(200, facade.jobs.create(
                        body["type"], body.get("params", {}),
                        requester=body.get("requester")))
                if path == "/jobs/claim":
                    claimed = facade.jobs.claim(body["worker_id"], body.get("types"))
                    return self._send(200, claimed or {})
                if path == "/datasets/contribute":
                    return self._send(200, facade.contribute_dataset(
                        name=body.get("name"), kind=body.get("kind", "contributed"),
                        rows=body.get("rows"), url=body.get("url"),
                        curate=body.get("curate", True),
                        contributor=body.get("contributor")))
                if path == "/demand/quote":
                    return self._send(200, facade.demand.quote(
                        body["job_type"], body.get("params", {}),
                        body["reward_anm"], requester=body.get("requester")))
                if path == "/demand/confirm":
                    return self._send(200, facade.demand.confirm(
                        body["job_id"], body["txid"]))
                if path == "/wallet/start":
                    return self._send(200, facade.walletconnect.start(body.get("appOrigin")))
                if path == "/wallet/callback":
                    return self._send(200, facade.walletconnect.callback(
                        body["requestId"], bool(body.get("approved")),
                        body.get("accounts", [])))
                if path == "/search":
                    return self._send(200, {"results": facade.search(
                        body["query"], mode=body.get("mode", "hybrid"),
                        index=body.get("index"))})
                if path.startswith("/jobs/") and path.endswith("/run"):
                    return self._send(200, facade.jobs.run(path.split("/")[2]))
                if path.startswith("/jobs/") and path.endswith("/verify"):
                    return self._send(200, facade.jobs.verify(path.split("/")[2]))
                if path.startswith("/jobs/") and path.endswith("/receipt"):
                    return self._send(200, facade.jobs.receipt(path.split("/")[2]))
                if path.startswith("/jobs/") and path.endswith("/export"):
                    return self._send(200, facade.jobs.export_onchain(path.split("/")[2]))
                return self._send(404, {"error": "not found", "path": path})
            except KeyError as exc:
                return self._send(400, {"error": f"missing field: {exc}"})
            except Exception as exc:  # noqa: BLE001
                return self._send(400, {"error": str(exc)})

    return Handler


def serve(facade, host: str = "127.0.0.1", port: int = 8787) -> None:
    httpd = ThreadingHTTPServer((host, port), _make_handler(facade))
    print(f"[ena] serving on http://{host}:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover
        pass
    finally:
        httpd.server_close()
