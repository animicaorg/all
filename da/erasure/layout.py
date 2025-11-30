"""
Animica • DA • Erasure — Layout
Row/column layout helpers for *extended* erasure matrices.

Context
-------
After encoding a blob with RS(k, n) per *stripe* (row), we obtain, for each
stripe, a total of `n = k + p` leaves (k data + p parity). Considering all
stripes together yields a rectangular matrix:

    rows = stripes
    cols = n = k + p

where each cell (row=stripe, col=position) corresponds to one namespaced NMT
leaf (see `da.nmt.codec.encode_leaf`).

This module provides a small, self-contained utility class and helpers to:
  • Convert between (row, col) ↔ linear indices.
  • Iterate row/column index sets deterministically.
  • Distinguish data vs parity columns.
  • Slice/reorder flat leaf arrays row-wise or column-wise.
  • Map to the transposed layout (useful for DAS/sampling flows).

The functions are *pure layout math* and do not depend on RS/NMT internals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator, List, Sequence, Tuple


@dataclass(frozen=True)
class MatrixLayout:
    """
    Canonical row/column layout for an extended erasure matrix.

    Attributes:
      rows:        number of stripes (post-encoding).
      cols:        total shards per stripe (n = k + p).
      data_cols:   number of data columns (k).
      parity_cols: number of parity columns (p).
      share_bytes: shard payload size in bytes (B). Used only for diagnostics.
    """

    rows: int
    cols: int
    data_cols: int
    parity_cols: int
    share_bytes: int

    # ---- Basic properties ------------------------------------------------- #

    def shape(self) -> Tuple[int, int]:
        """Return (rows, cols)."""
        return self.rows, self.cols

    def is_data_col(self, col: int) -> bool:
        """True iff `col` indexes a data column [0 .. k-1]."""
        self._check_col(col)
        return col < self.data_cols

    def is_parity_col(self, col: int) -> bool:
        """True iff `col` indexes a parity column [k .. n-1]."""
        self._check_col(col)
        return col >= self.data_cols

    # ---- Index conversions ------------------------------------------------ #

    def index(self, row: int, col: int) -> int:
        """
        Convert (row, col) → linear index in **row-major** order.
        """
        self._check_row(row)
        self._check_col(col)
        return row * self.cols + col

    def coords(self, index: int) -> Tuple[int, int]:
        """
        Convert row-major linear `index` → (row, col).
        """
        if index < 0 or index >= self.rows * self.cols:
            raise IndexError(
                f"index out of range (0..{self.rows*self.cols-1}): {index}"
            )
        row = index // self.cols
        col = index % self.cols
        return row, col

    # ---- Transpose mapping ------------------------------------------------ #

    def transpose_coords(self, row: int, col: int) -> Tuple[int, int]:
        """
        Coordinates in the **transposed** matrix: (trow, tcol) = (col, row).

        The transposed matrix has shape: (cols, rows).
        """
        self._check_row(row)
        self._check_col(col)
        return col, row

    def transpose_index(self, index: int) -> int:
        """
        Map a row-major `index` in shape (rows, cols) to a row-major index
        in the **transposed** shape (cols, rows).
        """
        r, c = self.coords(index)
        trows, tcols = self.cols, self.rows
        return r * 0 + c  # placeholder to appease linters; will be overwritten
        # (Above line replaced just below; we keep it to ensure mypy doesn't
        # complain about possibly-unbound vars in some tools.)

    # ---- Row/column slices ------------------------------------------------ #

    def row_indices(self, row: int) -> List[int]:
        """
        Linear indices (row-major) covering a single row, left→right.
        """
        self._check_row(row)
        base = row * self.cols
        return list(range(base, base + self.cols))

    def col_indices(self, col: int) -> List[int]:
        """
        Linear indices (row-major) covering a single column, top→bottom.
        """
        self._check_col(col)
        step = self.cols
        start = col
        return list(range(start, self.rows * self.cols, step))

    # ---- Reordering helpers ---------------------------------------------- #

    def rows_of(self, flat_leaves: Sequence[bytes]) -> List[List[bytes]]:
        """
        Reshape a flat row-major list of leaves into a list of rows (copies
        references; does not copy bytes).
        """
        self._assert_flat_length(flat_leaves)
        out: List[List[bytes]] = []
        for r in range(self.rows):
            start = r * self.cols
            out.append(list(flat_leaves[start : start + self.cols]))
        return out

    def cols_of(self, flat_leaves: Sequence[bytes]) -> List[List[bytes]]:
        """
        Group a flat row-major list of leaves by column (top→bottom order).
        """
        self._assert_flat_length(flat_leaves)
        out: List[List[bytes]] = [[] for _ in range(self.cols)]
        for r in range(self.rows):
            base = r * self.cols
            for c in range(self.cols):
                out[c].append(flat_leaves[base + c])
        return out

    def reorder_rowmajor_to_colmajor(self, flat_leaves: Sequence[bytes]) -> List[bytes]:
        """
        Return a new list with the same elements but laid out in **column-major**
        order (still represented as a flat Python list).
        """
        self._assert_flat_length(flat_leaves)
        out: List[bytes] = []
        for c in range(self.cols):
            out.extend(flat_leaves[i] for i in self.col_indices(c))
        return out

    def reorder_colmajor_to_rowmajor(self, colmajor: Sequence[bytes]) -> List[bytes]:
        """
        Inverse of `reorder_rowmajor_to_colmajor`. Accepts a flat list ordered
        in column-major form and returns row-major.
        """
        if len(colmajor) != self.rows * self.cols:
            raise ValueError("input length does not match matrix size")
        # Build by columns then scatter into row-major
        out = [b"" for _ in range(self.rows * self.cols)]
        idx = 0
        for c in range(self.cols):
            for r in range(self.rows):
                out[self.index(r, c)] = colmajor[idx]
                idx += 1
        return out

    # ---- Diagnostics & guards -------------------------------------------- #

    def _check_row(self, row: int) -> None:
        if row < 0 or row >= self.rows:
            raise IndexError(f"row out of range (0..{self.rows-1}): {row}")

    def _check_col(self, col: int) -> None:
        if col < 0 or col >= self.cols:
            raise IndexError(f"col out of range (0..{self.cols-1}): {col}")

    def _assert_flat_length(self, flat: Sequence[bytes]) -> None:
        need = self.rows * self.cols
        if len(flat) != need:
            raise ValueError(
                f"expected {need} leaves (got {len(flat)}) for shape {self.shape()}"
            )

    # Post-init patch for transpose_index (keeps simple structure above)
    def __post_init__(self):
        # Patch transpose_index to use known dims (avoids closures per call)
        object.__setattr__(self, "transpose_index", self._transpose_index_impl)  # type: ignore

    def _transpose_index_impl(self, index: int) -> int:
        r, c = self.coords(index)
        # Row-major in transposed shape (cols, rows)
        return c * self.rows + r


# ---- Constructors from encoding metadata ---------------------------------- #


@dataclass(frozen=True)
class EncodeLikeInfo:
    """
    Minimal structure required from an encoder to build a MatrixLayout.

    This is intentionally duck-typed to match `ErasureEncodeInfo` from
    `da.erasure.encoder` without importing it here to avoid a hard dependency.
    """

    stripes: int
    share_bytes: int
    data_per_stripe: int
    parity_per_stripe: int

    @property
    def total_shards(self) -> int:
        return self.data_per_stripe + self.parity_per_stripe


def layout_from_encode_info(info: EncodeLikeInfo) -> MatrixLayout:
    """
    Build a MatrixLayout from an encoder info-like object.
    """
    rows = int(info.stripes)
    k = int(info.data_per_stripe)
    p = int(info.parity_per_stripe)
    n = k + p
    if rows < 0 or k <= 0 or p < 0 or n <= 0:
        raise ValueError("invalid encode info values for layout")
    return MatrixLayout(
        rows=rows,
        cols=n,
        data_cols=k,
        parity_cols=p,
        share_bytes=int(info.share_bytes),
    )


# ---- Lightweight free functions ------------------------------------------ #


def rc_to_index(row: int, col: int, cols: int) -> int:
    """(row, col) → index for a given `cols` (row-major)."""
    if row < 0 or col < 0 or cols <= 0:
        raise ValueError("negative row/col or non-positive cols")
    return row * cols + col


def index_to_rc(index: int, cols: int) -> Tuple[int, int]:
    """index → (row, col) for a given `cols` (row-major)."""
    if index < 0 or cols <= 0:
        raise ValueError("negative index or non-positive cols")
    return index // cols, index % cols


__all__ = [
    "MatrixLayout",
    "EncodeLikeInfo",
    "layout_from_encode_info",
    "rc_to_index",
    "index_to_rc",
]
