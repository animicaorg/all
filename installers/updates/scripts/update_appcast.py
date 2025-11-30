#!/usr/bin/env python3
"""
Animica — Sparkle Appcast updater

Injects the latest version entry into a Sparkle v2 appcast.xml, computing:
  - exact byte length of the artifact (DMG/PKG)
  - Ed25519 signature (Sparkle 'edSignature') if a private key PEM is provided
  - pubDate (RFC-2822)
  - release notes link

It preserves older <item> entries for rollback/testing and inserts the new one
at the top of the channel feed.

Requirements:
  - Python 3.8+
  - One of:
      * cryptography (preferred)
      * pynacl (fallback)
  - Private key: Ed25519 PEM (Sparkle v2). If encrypted, pass --pem-passphrase
    OR set env SPARKLE_PRIV_PASSPHRASE.

Usage:
  python installers/updates/scripts/update_appcast.py \
    --app wallet \
    --channel stable \
    --version 1.4.3 \
    --artifact dist/Animica-Wallet-1.4.3.dmg \
    --notes https://animica.dev/changelog/wallet/1.4.3 \
    --private-key installers/wallet/macos/sparkle/ed25519_private.pem \
    --feed-root installers/updates \
    --min-os 11.0

This will update:
  installers/updates/wallet/stable/appcast.xml

Exit codes:
  0  success
  2  invalid args
  3  signing error
  4  IO/XML error
"""

from __future__ import annotations

import argparse
import base64
import email.utils
import getpass
import hashlib
import mmap
import os
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, Tuple

SPARKLE_NS = "http://www.andymatuschak.org/xml-namespaces/sparkle"
DC_NS = "http://purl.org/dc/elements/1.1/"

ET.register_namespace("sparkle", SPARKLE_NS)
ET.register_namespace("dc", DC_NS)


def _indent(elem: ET.Element, level: int = 0) -> None:
    """Pretty-print indent (in-place) for ElementTree."""
    i = "\n" + level * "  "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + "  "
        for e in elem:
            _indent(e, level + 1)
        if not e.tail or not e.tail.strip():  # type: ignore[name-defined]
            e.tail = i
    if level and (not elem.tail or not elem.tail.strip()):
        elem.tail = i


def _rfc2822_now() -> str:
    return email.utils.formatdate(time.time(), usegmt=True)


def _calc_len_and_sha256(artifact: Path) -> Tuple[int, str]:
    h = hashlib.sha256()
    size = 0
    with artifact.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            if not chunk:
                break
            size += len(chunk)
            h.update(chunk)
    return size, h.hexdigest()


def _load_ed25519_key(private_key_pem: Path, passphrase: Optional[str]) -> object:
    """
    Loads an Ed25519 private key. Returns a signer object with a .sign(data: bytes)->bytes API.
    Attempts cryptography first, then pynacl.
    """
    pem_bytes = private_key_pem.read_bytes()
    # Try cryptography
    try:
        from cryptography.hazmat.primitives.serialization import \
            load_pem_private_key

        key = load_pem_private_key(
            pem_bytes,
            password=None if passphrase is None else passphrase.encode("utf-8"),
        )
        # Ensure correct type
        from cryptography.hazmat.primitives.asymmetric.ed25519 import \
            Ed25519PrivateKey

        if not isinstance(key, Ed25519PrivateKey):
            raise TypeError("PEM is not an Ed25519 private key")
        return key  # cryptography key has .sign()
    except Exception as e:
        cryptography_err = e
    else:
        cryptography_err = None

    # Fallback: PyNaCl (expects raw seed; handle PEM via cryptography if possible)
    try:
        from nacl import signing
        from nacl.encoding import RawEncoder

        # If we got here, cryptography failed (maybe not installed). Try to parse PEM manually:
        # Many Sparkle PEMs are in "PRIVATE KEY" PKCS#8; parsing that without cryptography is messy.
        # To keep this robust, require cryptography for PEM; but still allow NaCl if the user
        # points to a raw 32-byte seed file via --private-key.
        if pem_bytes.startswith(b"\x00") or len(pem_bytes) == 32:
            seed = pem_bytes[:32]
            sk = signing.SigningKey(seed, encoder=RawEncoder)
            return sk  # PyNaCl key with .sign()
        raise RuntimeError(
            "PyNaCl fallback only supports raw 32-byte seed files. Install 'cryptography' to load PEM."
        )
    except Exception as e:
        if cryptography_err:
            raise RuntimeError(
                f"Failed to load Ed25519 key with 'cryptography' ({cryptography_err}); "
                f"and PyNaCl fallback failed: {e}"
            )
        raise


def _sign_ed25519(signer: object, blob: memoryview) -> bytes:
    """
    Signs the artifact bytes using the provided signer.
    Supports cryptography and pynacl signers.
    """
    # cryptography
    try:
        return signer.sign(blob)  # type: ignore[attr-defined]
    except Exception:
        pass
    # pynacl
    try:
        sig = signer.sign(bytes(blob))  # type: ignore[attr-defined]
        # PyNaCl returns signed message; signature is first 64 bytes if using detached?
        # signing.SigningKey.sign returns a SignedMessage (sig+msg). Extract first 64 bytes.
        return sig.signature  # type: ignore[attr-defined]
    except Exception as e:
        raise RuntimeError(f"Ed25519 signing failed: {e}")


def _compute_ed25519_signature(
    artifact: Path, private_key_pem: Path, passphrase: Optional[str]
) -> str:
    signer = _load_ed25519_key(private_key_pem, passphrase)
    with artifact.open("rb") as f, mmap.mmap(
        f.fileno(), 0, access=mmap.ACCESS_READ
    ) as mm:
        sig_bytes = _sign_ed25519(signer, memoryview(mm))
    return base64.b64encode(sig_bytes).decode("ascii")


def _ensure_skeleton(appcast_path: Path, title: str, feed_url: str) -> ET.ElementTree:
    if appcast_path.exists():
        return ET.parse(appcast_path)
    # Build minimal skeleton
    rss = ET.Element("rss", {"version": "2.0"})
    rss.set(f"{{{SPARKLE_NS}}}dummy", "")  # ensure namespace is declared
    rss.set(f"{{{DC_NS}}}dummy", "")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = title
    ET.SubElement(channel, "link").text = feed_url
    ET.SubElement(channel, "description").text = title
    ET.SubElement(channel, "language").text = "en"
    ET.SubElement(channel, f"{{{SPARKLE_NS}}}publisher").text = "Animica Labs"
    ET.SubElement(channel, f"{{{SPARKLE_NS}}}minimumAutoupdateVersion").text = "1.0.0"
    return ET.ElementTree(rss)


def _build_item(
    version: str,
    notes_url: str,
    artifact_url: str,
    length_bytes: int,
    ed_sig_b64: str,
    min_os: str,
    os_name: str = "macos",
    short_version: Optional[str] = None,
    sha256: Optional[str] = None,
    pub_date: Optional[str] = None,
) -> ET.Element:
    item = ET.Element("item")
    ET.SubElement(item, "title").text = (
        f"Animica {'Wallet' if 'wallet' in artifact_url.lower() else 'Explorer'} {version}"
    )
    ET.SubElement(item, f"{{{SPARKLE_NS}}}releaseNotesLink").text = notes_url
    ET.SubElement(item, "pubDate").text = pub_date or _rfc2822_now()

    # enclosure with Sparkle attrs
    enc = ET.SubElement(item, "enclosure")
    enc.set("url", artifact_url)
    enc.set(f"{{{SPARKLE_NS}}}version", short_version or version)
    enc.set(f"{{{SPARKLE_NS}}}shortVersionString", short_version or version)
    enc.set(f"{{{SPARKLE_NS}}}os", os_name)
    enc.set(f"{{{SPARKLE_NS}}}minimumSystemVersion", min_os)
    enc.set("length", str(length_bytes))
    enc.set("type", "application/octet-stream")
    if ed_sig_b64:
        enc.set(f"{{{SPARKLE_NS}}}edSignature", ed_sig_b64)
    if sha256:
        # Not required by Sparkle, but harmless metadata for our sanity
        enc.set("sparkle:sha256", sha256)  # left as literal to avoid extra namespace
    return item


def _insert_item_at_top(
    tree: ET.ElementTree, new_item: ET.Element, version: str
) -> None:
    ch = tree.getroot().find("./channel")
    if ch is None:
        raise ValueError("Invalid appcast: missing <channel>")
    # Remove any existing item with same version (by matching title suffix or enclosure@version)
    to_remove = []
    for item in ch.findall("./item"):
        title = item.findtext("title") or ""
        enc = item.find("./enclosure")
        v_match = False
        if title.strip().endswith(version):
            v_match = True
        elif enc is not None:
            v_attr = enc.get(f"{{{SPARKLE_NS}}}version") or enc.get("sparkle:version")
            if (v_attr or "").strip() == version:
                v_match = True
        if v_match:
            to_remove.append(item)
    for it in to_remove:
        ch.remove(it)
    # Insert at top (after any channel-level metadata)
    first_item = ch.find("./item")
    if first_item is None:
        ch.append(new_item)
    else:
        ch.insert(list(ch).index(first_item), new_item)


def update_appcast(
    app: str,
    channel: str,
    version: str,
    artifact_path: Path,
    notes_url: str,
    feed_root: Path,
    artifact_url: Optional[str],
    private_key: Optional[Path],
    pem_passphrase: Optional[str],
    min_os: str,
) -> Path:
    if app not in {"wallet", "explorer"}:
        raise SystemExit(f"Unsupported --app '{app}' (expected wallet|explorer)")
    if channel not in {"stable", "beta", "dev"}:
        raise SystemExit(
            f"Unsupported --channel '{channel}' (expected stable|beta|dev)"
        )
    if not artifact_path.is_file():
        raise SystemExit(f"Artifact not found: {artifact_path}")

    # Compute len + sha256
    length, sha256 = _calc_len_and_sha256(artifact_path)

    # Signature (optional)
    ed_sig_b64 = ""
    if private_key:
        try:
            ed_sig_b64 = _compute_ed25519_signature(
                artifact_path, private_key, pem_passphrase
            )
        except Exception as e:
            print(f"[!] Signing failed: {e}", file=sys.stderr)
            raise SystemExit(3)

    # Determine appcast path & URLs
    appcast_dir = feed_root / app / channel
    appcast_dir.mkdir(parents=True, exist_ok=True)
    appcast_path = appcast_dir / "appcast.xml"
    feed_url = f"https://updates.animica.dev/{app}/{channel}/appcast.xml"

    # Determine artifact URL if not provided
    if not artifact_url:
        artifact_url = (
            f"https://updates.animica.dev/{app}/{channel}/{artifact_path.name}"
        )

    # Ensure skeleton
    title = f"Animica {'Wallet' if app=='wallet' else 'Explorer'} Updates ({channel})"
    try:
        tree = _ensure_skeleton(appcast_path, title=title, feed_url=feed_url)
    except Exception as e:
        print(f"[!] Failed to read/create appcast: {e}", file=sys.stderr)
        raise SystemExit(4)

    # Build new item & insert
    item = _build_item(
        version=version,
        notes_url=notes_url,
        artifact_url=artifact_url,
        length_bytes=length,
        ed_sig_b64=ed_sig_b64 or "REPLACE_WITH_ED25519_SIGNATURE_BASE64",
        min_os=min_os,
        sha256=sha256,
        pub_date=_rfc2822_now(),
    )
    _insert_item_at_top(tree, item, version)

    # Pretty & write
    _indent(tree.getroot())
    try:
        tree.write(appcast_path, encoding="utf-8", xml_declaration=True)
    except Exception as e:
        print(f"[!] Failed to write appcast: {e}", file=sys.stderr)
        raise SystemExit(4)

    print(f"[✓] Updated {appcast_path}")
    print(f"    version={version}")
    print(f"    artifact={artifact_path} (len={length} sha256={sha256})")
    if ed_sig_b64:
        print(f"    edSignature={ed_sig_b64[:16]}… ({len(ed_sig_b64)} b64 chars)")
    else:
        print("    edSignature=REPLACE_WITH_ED25519_SIGNATURE_BASE64 (no key provided)")
    print(f"    notes={notes_url}")
    print(f"    url={artifact_url}")
    return appcast_path


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Update Sparkle appcast with latest release."
    )
    p.add_argument(
        "--app", required=True, choices=["wallet", "explorer"], help="App name"
    )
    p.add_argument(
        "--channel",
        required=True,
        choices=["stable", "beta", "dev"],
        help="Release channel",
    )
    p.add_argument(
        "--version", required=True, help="Version string (e.g., 1.4.3 or 1.5.0-beta.2)"
    )
    p.add_argument(
        "--artifact", required=True, type=Path, help="Path to DMG/PKG to publish"
    )
    p.add_argument("--notes", required=True, help="Release notes URL")
    p.add_argument(
        "--feed-root",
        type=Path,
        default=Path("installers/updates"),
        help="Root dir where appcast.xml lives",
    )
    p.add_argument(
        "--artifact-url",
        help="Override artifact URL; defaults to https://updates.animica.dev/{app}/{channel}/{filename}",
    )
    p.add_argument(
        "--private-key",
        type=Path,
        help="Path to Sparkle Ed25519 private key PEM (optional)",
    )
    p.add_argument(
        "--pem-passphrase",
        help="Passphrase for encrypted PEM (optional; will prompt if needed)",
    )
    p.add_argument(
        "--min-os", default="11.0", help="sparkle:minimumSystemVersion (default: 11.0)"
    )
    args = p.parse_args(argv)

    pem_pw = args.pem_passphrase or os.getenv("SPARKLE_PRIV_PASSPHRASE")
    if args.private_key and pem_pw is None:
        # Attempt to detect encrypted key and prompt
        try:
            data = args.private_key.read_bytes()
            needs_pw = b"ENCRYPTED" in data or b"BEGIN ENCRYPTED PRIVATE KEY" in data
        except Exception:
            needs_pw = False
        if needs_pw:
            pem_pw = getpass.getpass("PEM passphrase: ")

    try:
        update_appcast(
            app=args.app,
            channel=args.channel,
            version=args.version,
            artifact_path=args.artifact,
            notes_url=args.notes,
            feed_root=args.feed_root,
            artifact_url=args.artifact_url,
            private_key=args.private_key,
            pem_passphrase=pem_pw,
            min_os=args.min_os,
        )
    except SystemExit as e:
        return int(e.code)
    except Exception as e:
        print(f"[!] Unhandled error: {e}", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
