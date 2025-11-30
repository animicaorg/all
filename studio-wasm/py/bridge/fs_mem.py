from __future__ import annotations

"""
A tiny in-memory POSIX-like filesystem for Pyodide tests/examples.

Goals
-----
- Pure-Python, zero deps, browser-safe (no OS access).
- Deterministic behavior suitable for unit tests and demo flows.
- Minimal API: cwd, mkdirs, read/write bytes/text, listdir, walk, stat.

Design
------
- Paths are POSIX-style ("/"-separated) and normalized.
- The root directory "/" always exists.
- Directories are tracked explicitly; files are a mapping path -> bytes.
- Timestamps are best-effort (float seconds via time.time()) but not guaranteed
  to be monotonic in all execution contexts (OK for tests).

This is intentionally small; extend only as needed for studio-wasm usage.
"""

import time
from dataclasses import dataclass
from typing import Dict, Generator, Iterable, Iterator, List, Optional, Tuple

# ----------------------------- Data Types -----------------------------


@dataclass(frozen=True)
class FileStat:
    path: str
    is_dir: bool
    size: int
    mtime: float


# ----------------------------- Helpers -------------------------------


def _now() -> float:
    try:
        return time.time()
    except Exception:
        return 0.0


def _split(path: str) -> List[str]:
    # Split a POSIX path into parts (no empty except possible root marker)
    parts = []
    for p in path.split("/"):
        if p == "" or p == ".":
            continue
        if p == "..":
            if parts:
                parts.pop()
            continue
        parts.append(p)
    return parts


def _join(*parts: str) -> str:
    return "/".join(p.strip("/") for p in parts if p != "")


def _normalize(cwd: str, path: str) -> str:
    if not path:
        return cwd
    if path.startswith("/"):
        base = "/"
        segs = _split(path)
    else:
        base = "/"
        segs = _split(_join(cwd, path))
    out: List[str] = []
    for s in segs:
        if s == "..":
            if out:
                out.pop()
        elif s == "." or s == "":
            continue
        else:
            out.append(s)
    return "/" + "/".join(out)


def _parent(path: str) -> str:
    if path == "/":
        return "/"
    parts = _split(path)
    if not parts:
        return "/"
    parts.pop()
    return "/" + "/".join(parts)


def _basename(path: str) -> str:
    if path == "/":
        return ""
    parts = _split(path)
    return parts[-1] if parts else ""


# ----------------------------- MemFS ---------------------------------


class MemFS:
    """
    A simple in-memory filesystem with a subset of POSIX-like operations.

    Not thread-safe by design (Pyodide workers typically use single-threaded
    execution). If you need cross-thread use, wrap calls in your own lock.
    """

    def __init__(self) -> None:
        self._dirs: Dict[str, float] = {"/": _now()}  # dir path -> mtime
        self._files: Dict[str, bytes] = {}  # file path -> content
        self._mtimes: Dict[str, float] = {}  # file path -> mtime
        self._cwd: str = "/"

    # ---- cwd ----

    def getcwd(self) -> str:
        return self._cwd

    def chdir(self, path: str) -> None:
        p = _normalize(self._cwd, path)
        if not self.isdir(p):
            raise FileNotFoundError(f"directory not found: {path}")
        self._cwd = p

    # ---- path predicates ----

    def exists(self, path: str) -> bool:
        p = _normalize(self._cwd, path)
        return p in self._dirs or p in self._files

    def isdir(self, path: str) -> bool:
        p = _normalize(self._cwd, path)
        return p in self._dirs

    def isfile(self, path: str) -> bool:
        p = _normalize(self._cwd, path)
        return p in self._files

    # ---- mkdirs ----

    def makedirs(self, path: str, exist_ok: bool = True) -> None:
        p = _normalize(self._cwd, path)
        if p in self._files:
            raise FileExistsError(f"file exists at directory path: {p}")
        # create parents
        parts = _split(p)
        cur = "/"
        for part in parts:
            cur = f"{cur}{part}" if cur == "/" else f"{cur}/{part}"
            if cur not in self._dirs:
                self._dirs[cur] = _now()
        if not exist_ok and p in self._dirs:
            raise FileExistsError(f"directory exists: {p}")

    # ---- read / write ----

    def write_bytes(self, path: str, data: bytes, *, makedirs: bool = True) -> None:
        p = _normalize(self._cwd, path)
        parent = _parent(p)
        if parent not in self._dirs:
            if makedirs:
                self.makedirs(parent, exist_ok=True)
            else:
                raise FileNotFoundError(f"parent directory missing: {parent}")
        self._files[p] = bytes(data)
        ts = _now()
        self._mtimes[p] = ts
        # touch parent mtime
        self._dirs[parent] = ts

    def write_text(
        self, path: str, text: str, encoding: str = "utf-8", *, makedirs: bool = True
    ) -> None:
        self.write_bytes(path, text.encode(encoding), makedirs=makedirs)

    def read_bytes(self, path: str) -> bytes:
        p = _normalize(self._cwd, path)
        try:
            return self._files[p]
        except KeyError:
            if p in self._dirs:
                raise IsADirectoryError(f"is a directory: {p}")
            raise FileNotFoundError(f"file not found: {p}")

    def read_text(self, path: str, encoding: str = "utf-8") -> str:
        return self.read_bytes(path).decode(encoding)

    # ---- open-like helpers (minimal) ----

    def open(self, path: str, mode: str = "rb", encoding: str = "utf-8"):
        """
        Minimal context-manager file handle.

        Supported modes: 'rb', 'wb', 'ab', 'rt', 'wt', 'at'.
        """
        p = _normalize(self._cwd, path)
        bin_mode = "b" in mode
        write = "w" in mode or "a" in mode
        append = "a" in mode

        # prepare parent
        parent = _parent(p)
        if write and parent not in self._dirs:
            self.makedirs(parent, exist_ok=True)

        # initial buffer
        initial = self._files.get(p, b"")
        buf = bytearray(initial if append else b"")

        fs = self

        class _Handle:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, exc_type, exc, tb):
                if exc is None and write:
                    fs.write_bytes(p, bytes(buf))
                return False

            def write(self_inner, data):
                nonlocal buf
                if not write:
                    raise IOError("file not open for writing")
                if bin_mode:
                    if isinstance(data, (bytes, bytearray, memoryview)):
                        buf.extend(data)
                    else:
                        raise TypeError("write() requires bytes in binary mode")
                else:
                    if not isinstance(data, str):
                        raise TypeError("write() requires str in text mode")
                    buf.extend(data.encode(encoding))
                return len(data)

            def read(self_inner, n: int = -1):
                data = bytes(initial) if not append else bytes(buf)
                if bin_mode:
                    out = data if n < 0 else data[:n]
                    return out
                else:
                    txt = data.decode(encoding)
                    return txt if n < 0 else txt[:n]

        # reading without writing: ensure existence
        if not write and p not in self._files:
            if p in self._dirs:
                raise IsADirectoryError(f"is a directory: {p}")
            raise FileNotFoundError(f"file not found: {p}")

        return _Handle()

    # ---- delete ----

    def remove(self, path: str) -> None:
        p = _normalize(self._cwd, path)
        if p in self._files:
            del self._files[p]
            self._mtimes.pop(p, None)
            # touch parent mtime
            par = _parent(p)
            if par in self._dirs:
                self._dirs[par] = _now()
            return
        if p in self._dirs:
            raise IsADirectoryError(f"is a directory: {p}")
        raise FileNotFoundError(f"no such file: {p}")

    def rmdir(self, path: str, *, recursive: bool = False) -> None:
        p = _normalize(self._cwd, path)
        if p == "/":
            raise PermissionError("cannot remove root directory")
        if p not in self._dirs:
            if p in self._files:
                raise NotADirectoryError(f"not a directory: {p}")
            raise FileNotFoundError(f"no such directory: {p}")

        children = self.listdir(p)
        if children and not recursive:
            raise OSError(f"directory not empty: {p}")
        # Recursively remove
        if recursive:
            # remove files under p
            to_del_files = [f for f in self._files if f == p or f.startswith(p + "/")]
            for f in to_del_files:
                self.remove(f)
            # remove subdirs (deepest-first)
            to_del_dirs = sorted(
                [
                    d
                    for d in self._dirs
                    if d != "/" and (d == p or d.startswith(p + "/"))
                ],
                key=len,
                reverse=True,
            )
            for d in to_del_dirs:
                if d in self._dirs:
                    del self._dirs[d]
        else:
            del self._dirs[p]

        # touch parent mtime
        par = _parent(p)
        if par in self._dirs:
            self._dirs[par] = _now()

    # ---- listing / walk ----

    def listdir(self, path: str = ".") -> List[str]:
        p = _normalize(self._cwd, path)
        if p not in self._dirs:
            if p in self._files:
                raise NotADirectoryError(f"not a directory: {p}")
            raise FileNotFoundError(f"no such directory: {p}")
        names: List[str] = []
        prefix = "" if p == "/" else p + "/"
        plen = len(prefix)
        # immediate children: files
        for f in self._files:
            if f.startswith(prefix):
                rest = f[plen:]
                if "/" not in rest and rest != "":
                    names.append(rest)
        # immediate children: dirs
        for d in self._dirs:
            if d == p:
                continue
            if d.startswith(prefix):
                rest = d[plen:]
                if rest != "" and "/" not in rest:
                    names.append(rest)
        names = sorted(set(names))
        return names

    def walk(self, top: str = ".") -> Iterator[Tuple[str, List[str], List[str]]]:
        """
        Generate (dirpath, dirnames, filenames) tuples similar to os.walk.
        """
        start = _normalize(self._cwd, top)
        if start not in self._dirs:
            raise FileNotFoundError(f"no such directory: {top}")
        stack = [start]
        while stack:
            cur = stack.pop()
            dirs: List[str] = []
            files: List[str] = []
            for name in self.listdir(cur):
                child = cur if cur == "/" else f"{cur}/{name}"
                if child in self._dirs:
                    dirs.append(name)
                elif child in self._files:
                    files.append(name)
            yield (cur, sorted(dirs), sorted(files))
            # push subdirs
            for d in sorted(dirs, reverse=True):
                nxt = cur if cur == "/" else f"{cur}/{d}"
                stack.append(nxt)

    # ---- metadata ----

    def stat(self, path: str) -> FileStat:
        p = _normalize(self._cwd, path)
        if p in self._files:
            size = len(self._files[p])
            mtime = self._mtimes.get(p, 0.0)
            return FileStat(path=p, is_dir=False, size=size, mtime=mtime)
        if p in self._dirs:
            return FileStat(path=p, is_dir=True, size=0, mtime=self._dirs[p])
        raise FileNotFoundError(f"no such path: {p}")

    # ---- debugging ----

    def to_dict(self) -> Dict[str, bytes]:
        """Return a shallow copy of file contents mapping (for assertions)."""
        return dict(self._files)


__all__ = ["MemFS", "FileStat"]
