from __future__ import annotations

import base64
import io
import json
import os
import sys
from typing import Any


def _load_segno():
    if os.environ.get("ANIMICA_WALLET_QR_FORCE_IMPORT_ERROR"):
        raise RuntimeError("Forced QR dependency failure for testing.")

    try:
        import segno  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "Python QR dependency 'segno' is not installed. Rebuild the bundled wallet runtime "
            "or install the wallet_qt extras for the Animica package."
        ) from exc
    return segno


def render_png(payload: str, pixel_size: int = 512) -> bytes:
    segno = _load_segno()
    qr = segno.make(payload, error="m")

    border = 2
    symbol_width, _ = qr.symbol_size(scale=1, border=border)
    scale = max(1, int(pixel_size) // max(1, int(symbol_width)))

    buffer = io.BytesIO()
    qr.save(buffer, kind="png", scale=scale, border=border, dark="#000000", light="#ffffff")
    return buffer.getvalue()


def _response(ok: bool, **payload: Any) -> dict[str, Any]:
    body = {"ok": ok}
    body.update(payload)
    return body


def main() -> int:
    try:
        request = json.load(sys.stdin)
        payload = str(request.get("payload") or "").strip()
        pixel_size = int(request.get("pixel_size") or 512)
        if not payload:
            response = _response(
                False,
                error_kind="invalid_request",
                error_summary="QR payload is required.",
                error_details="The wallet did not provide a payload to encode.",
            )
            print(json.dumps(response))
            return 0

        png_bytes = render_png(payload, pixel_size=pixel_size)
        print(
            json.dumps(
                _response(
                    True,
                    png_base64=base64.b64encode(png_bytes).decode("ascii"),
                )
            )
        )
        return 0
    except Exception as exc:
        detail = str(exc)
        dependency_missing = any(token in detail.lower() for token in ("segno", "pypng", "dependency", "png support"))
        response = _response(
            False,
            error_kind="dependency_missing" if dependency_missing else "render_failed",
            error_summary="QR generation dependency missing."
            if dependency_missing
            else "QR generation failed.",
            error_details=detail,
        )
        print(json.dumps(response))
        return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
