"""Container image specification — the Modal ``Image`` analogue.

An ``Image`` is a *declarative* description of the environment a function runs
in: base, OS packages, pip packages, env vars, extra shell steps. It is
immutable and chainable; each builder method returns a new ``Image``.

Crucially the spec hashes to a deterministic ``ref`` (``studio-img:<sha256[:16]>``).
That ref is what travels in a job and what a provider keys its warm pool on.

What actually *builds* the image depends on the executor:

* **local-dev sandbox**  — `pip_install` packages are installed into the sandbox
  venv at invocation time (the `packages` hook the sandbox-runner already has).
* **distributed (target)** — the ref resolves to an OCI image pulled into a
  Firecracker/gVisor microVM. That puller is the genuine infra build-out tracked
  in ROADMAP.md; until it lands, a function pinned to a non-base image runs only
  on workers that already have it (or in local mode).

The spec is intentionally a plain serializable dict so the registry and the
provider can reason about it without importing this module.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence

_DEFAULT_PYTHON = "3.11"


@dataclass(frozen=True)
class Image:
    base: str = f"python:{_DEFAULT_PYTHON}-slim"
    python_version: str = _DEFAULT_PYTHON
    apt_packages: tuple = ()
    pip_packages: tuple = ()
    commands: tuple = ()
    env_vars: tuple = ()  # tuple of (key, value)
    registry_ref: Optional[str] = None  # if set, an explicit OCI tag overrides `base`

    # ---- builders (each returns a new, frozen Image) ----------------------

    @staticmethod
    def debian_slim(python_version: str = _DEFAULT_PYTHON) -> "Image":
        return Image(base=f"python:{python_version}-slim", python_version=python_version)

    @staticmethod
    def from_registry(tag: str, *, python_version: str = _DEFAULT_PYTHON) -> "Image":
        """Pin an explicit OCI image (e.g. ``nvidia/cuda:12.4.1-runtime``)."""
        return Image(base=tag, python_version=python_version, registry_ref=tag)

    def pip_install(self, *packages: str) -> "Image":
        pkgs = tuple(p for p in _flatten(packages) if p)
        return replace(self, pip_packages=self.pip_packages + pkgs)

    def apt_install(self, *packages: str) -> "Image":
        pkgs = tuple(p for p in _flatten(packages) if p)
        return replace(self, apt_packages=self.apt_packages + pkgs)

    def run_commands(self, *commands: str) -> "Image":
        cmds = tuple(c for c in _flatten(commands) if c)
        return replace(self, commands=self.commands + cmds)

    def env(self, **vars: str) -> "Image":
        merged = dict(self.env_vars)
        merged.update({str(k): str(v) for k, v in vars.items()})
        return replace(self, env_vars=tuple(sorted(merged.items())))

    # ---- serialization / identity -----------------------------------------

    def to_spec(self) -> Dict[str, object]:
        return {
            "base": self.base,
            "python_version": self.python_version,
            "apt": list(self.apt_packages),
            "pip": list(self.pip_packages),
            "commands": list(self.commands),
            "env": dict(self.env_vars),
            "registry_ref": self.registry_ref,
        }

    @property
    def ref(self) -> str:
        """Deterministic content-addressed id for this image spec."""
        canonical = json.dumps(self.to_spec(), sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
        return f"studio-img:{digest}"

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        extras = []
        if self.pip_packages:
            extras.append(f"pip={list(self.pip_packages)}")
        if self.apt_packages:
            extras.append(f"apt={list(self.apt_packages)}")
        return f"Image(base={self.base!r}{', ' + ', '.join(extras) if extras else ''}, ref={self.ref})"


def _flatten(items: Sequence) -> List[str]:
    """Allow both ``pip_install('a', 'b')`` and ``pip_install(['a', 'b'])``."""
    out: List[str] = []
    for it in items:
        if isinstance(it, (list, tuple)):
            out.extend(str(x) for x in it)
        else:
            out.append(str(it))
    return out


#: The implicit environment for a function that declares no image.
DEFAULT_IMAGE = Image.debian_slim()
