"""Secrets — named key/value bundles injected into a function's environment.

Dev/local: values live in the local process (``from_dict``) or are read from the
host environment (``from_env``/``from_name``). They are passed to the sandbox as
environment variables for the duration of a call and never serialized into the
job ``spec`` that travels to the broker.

Distributed (target): a named secret is fetched from an encrypted store and
delivered to the assigned worker over an authenticated channel — see
ROADMAP.md. Until then, ``from_name`` resolves against the host environment so
the same code works locally.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, Iterable, Mapping


@dataclass(frozen=True)
class Secret:
    name: str
    _values: Dict[str, str] = field(default_factory=dict, repr=False)

    @staticmethod
    def from_dict(values: Mapping[str, str], *, name: str = "inline") -> "Secret":
        return Secret(name=name, _values={str(k): str(v) for k, v in values.items()})

    @staticmethod
    def from_env(keys: Iterable[str], *, name: str = "env") -> "Secret":
        """Capture specific host env vars into a secret (raises if any missing)."""
        vals = {}
        missing = []
        for k in keys:
            if k in os.environ:
                vals[k] = os.environ[k]
            else:
                missing.append(k)
        if missing:
            raise KeyError(f"Secret.from_env missing host vars: {missing}")
        return Secret(name=name, _values=vals)

    @staticmethod
    def from_name(name: str) -> "Secret":
        """Resolve a named secret. Local fallback: any ``<NAME>__<KEY>`` env vars.

        e.g. a secret ``hf`` reads ``HF__HF_TOKEN`` -> ``{"HF_TOKEN": ...}``.
        """
        prefix = f"{name.upper()}__"
        vals = {k[len(prefix):]: v for k, v in os.environ.items() if k.startswith(prefix)}
        return Secret(name=name, _values=vals)

    def as_env(self) -> Dict[str, str]:
        return dict(self._values)
