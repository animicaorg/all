"""
The anm:// custom URL scheme — the heart of the .anm-only browser.

`register_anm_scheme()` MUST run before the QApplication is constructed. The handler resolves
`anm://<name>/...`, fetches the CID-addressed HTML, VERIFIES sha3_256(bytes) == CID locally,
and serves it as an opaque, sandboxed origin. A CID mismatch, an unregistered name, or an
endpoint-only record is turned into a generated card page — never clearnet content, and never
unverified bytes.

Resolution runs on a worker thread (QThreadPool) and replies on the UI thread via a queued
invoke, so navigation never freezes the window.
"""

from __future__ import annotations

import html
from typing import Optional

import threading

from PySide6.QtCore import (QBuffer, QByteArray, QMetaObject, QObject, QRunnable,
                            Q_ARG, Qt, QThreadPool, QUrl, Slot)
from PySide6.QtWebEngineCore import (QWebEngineUrlScheme, QWebEngineUrlSchemeHandler,
                                     QWebEngineUrlRequestJob)

from . import resolver
from .config import ANM_SCHEME


def register_anm_scheme() -> None:
    """Register the secure, CORS-enabled anm:// scheme. Call BEFORE QApplication()."""
    scheme = QWebEngineUrlScheme(ANM_SCHEME.encode())
    scheme.setSyntax(QWebEngineUrlScheme.Syntax.Host)
    scheme.setDefaultPort(QWebEngineUrlScheme.SpecialPort.PortUnspecified)
    # Secure origin (so modern web features work), CORS-enabled so sandboxed .anm pages may call
    # the CORS-open names/content API, but NO local-file access.
    scheme.setFlags(
        QWebEngineUrlScheme.Flag.SecureScheme
        | QWebEngineUrlScheme.Flag.CorsEnabled
        | QWebEngineUrlScheme.Flag.ContentSecurityPolicyIgnored
    )
    QWebEngineUrlScheme.registerScheme(scheme)


def _card(title: str, body_html: str) -> bytes:
    return (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<meta name='color-scheme' content='dark light'>"
            f"<title>{html.escape(title)}</title>"
            f"<style>body{{font:15px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,"
            f"Helvetica,Arial,sans-serif;background:#0A0E16;color:#E6ECF5;margin:0;"
            f"display:flex;min-height:100vh;align-items:center;justify-content:center}}"
            f".c{{max-width:560px;padding:40px;text-align:center}}"
            f"h1{{font-size:22px;margin:0 0 12px;color:#6EA8FE}}"
            f"a{{color:#B98CFF}} code{{color:#4FE3B0}}</style></head>"
            f"<body><div class='c'>{body_html}</div></body></html>").encode("utf-8")


class _ResolveTask(QRunnable):
    def __init__(self, handler: "AnmSchemeHandler", job: QWebEngineUrlRequestJob, name: str):
        super().__init__()
        self._handler = handler
        self._job = job
        self._name = name

    def run(self) -> None:
        status = "ok"
        mime = b"text/html"
        payload: bytes
        try:
            rn, content, endpoint = resolver.resolve_and_fetch(self._name)
            if content is not None:
                payload = content
            elif endpoint is not None:
                # Endpoint-type name. The browser handles endpoint navigation itself; if we are
                # asked to render it inline, show a card linking to it (we never proxy clearnet).
                payload = _card(rn.fqdn, f"<h1>{html.escape(rn.fqdn)}</h1>"
                                f"<p>This name points to an external endpoint.</p>"
                                f"<p><a href='{html.escape(endpoint)}'>{html.escape(endpoint)}</a></p>")
            else:
                payload = _card(rn.fqdn, f"<h1>{html.escape(rn.fqdn)}</h1>"
                                f"<p>Registered ({html.escape(rn.kind)}) but no content published yet.</p>")
        except resolver.ContentVerifyError as e:
            status = "verify"
            payload = _card("Content failed verification",
                            f"<h1>⚠ Untrusted content</h1><p>{html.escape(str(e))}</p>"
                            f"<p>The served bytes did not match their content hash, so "
                            f"<b>{html.escape(self._name)}.anm</b> was not rendered.</p>")
        except resolver.ResolveError as e:
            status = "notfound"
            payload = _card("Not found",
                            f"<h1>{html.escape(self._name)}.anm</h1><p>{html.escape(str(e))}</p>"
                            f"<p>Reserve it from the app's <b>Publish</b> tab.</p>")
        except Exception as e:  # noqa: BLE001 - never let the scheme handler crash the render
            status = "error"
            payload = _card("Error", f"<h1>Could not load</h1><p>{html.escape(str(e))}</p>")
        self._handler._deliver(self._job, payload, mime, status)


class AnmSchemeHandler(QWebEngineUrlSchemeHandler):
    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._pool = QThreadPool.globalInstance()
        self._pending: dict[int, tuple] = {}
        self._seq = 0
        self._lock = threading.Lock()

    def requestStarted(self, job: QWebEngineUrlRequestJob) -> None:  # noqa: N802 (Qt override)
        url: QUrl = job.requestUrl()
        name = url.host()
        if not name:
            job.fail(QWebEngineUrlRequestJob.Error.UrlInvalid)
            return
        self._pool.start(_ResolveTask(self, job, name))

    # Called from the worker thread; hop to the UI thread to actually reply (job.reply must run
    # on the object's thread).
    def _deliver(self, job: QWebEngineUrlRequestJob, payload: bytes, mime: bytes, status: str) -> None:
        with self._lock:
            self._seq += 1
            token = self._seq
            self._pending[token] = (job, payload, mime, status)
        QMetaObject.invokeMethod(self, "_reply_on_ui", Qt.QueuedConnection, Q_ARG(int, token))

    @Slot(int)
    def _reply_on_ui(self, token: int) -> None:
        with self._lock:
            job, payload, mime, _status = self._pending.pop(token, (None, b"", b"text/html", ""))
        if job is None:
            return
        buf = QBuffer(job)
        buf.setData(QByteArray(payload))
        buf.open(QBuffer.OpenModeFlag.ReadOnly)
        job.reply(QByteArray(mime), buf)
