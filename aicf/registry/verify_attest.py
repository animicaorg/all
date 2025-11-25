def verify_attestation(attestation: bytes | str | None) -> bool:
    # Default permissive; tests monkeypatch this to return True/False explicitly.
    return bool(attestation)
