"""Bounded reads for pipeline sources and persisted analysis exports."""
from __future__ import annotations

import ast
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import struct

import numpy as np

from wavebench.data.analysis_resources import AnalysisBudget, AnalysisLimits
from wavebench.errors import DataError
from wavebench.data.analysis_control import checkpoint


BLOCK_ROWS = 4096


def file_identity(stat):
    # Compare snapshots made by the same API: Windows stat and fstat may use
    # different file-ID representations (notably in Python 3.12).
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def read_json_bounded(path: Path, limits: AnalysisLimits):
    with path.open("rb") as file:
        limits.check("max_metadata_bytes", os.fstat(file.fileno()).st_size, "metadata")
        limits.check("max_working_bytes", os.fstat(file.fileno()).st_size * 32 + 65536, "metadata parsing")
        raw = file.read(limits.max_metadata_bytes + 1)
        limits.check("max_metadata_bytes", len(raw), "metadata")
    return json.loads(raw)


@contextmanager
def mapped_npy(path: Path, limits: AnalysisLimits, *, columns: int, source: bool = False):
    """Validate a bounded header and file size before mapping, then pin the opened inode."""
    array = None
    path_before = file_identity(path.stat())
    with path.open("rb") as file:
        before = file_identity(os.fstat(file.fileno()))
        if path_before != file_identity(path.stat()):
            raise DataError("analysis source changed while being opened")
        prefix = file.read(8)
        if len(prefix) != 8 or prefix[:6] != b"\x93NUMPY" or prefix[6:] not in (b"\x01\x00", b"\x02\x00", b"\x03\x00"):
            raise DataError("invalid or unsupported NPY header")
        version = prefix[6]
        length_bytes = file.read(2 if version == 1 else 4)
        if len(length_bytes) != (2 if version == 1 else 4):
            raise DataError("truncated NPY header")
        length = struct.unpack("<H" if version == 1 else "<I", length_bytes)[0]
        if length > 10000:
            raise DataError("NPY header exceeds 10000 bytes")
        raw = file.read(length)
        if len(raw) != length:
            raise DataError("truncated NPY header")
        try:
            header = ast.literal_eval(raw.decode("utf-8" if version == 3 else "latin1"))
            if not isinstance(header, dict) or set(header) != {"descr", "fortran_order", "shape"}:
                raise ValueError("invalid keys")
            shape, dtype, order = header["shape"], np.dtype(header["descr"]), header["fortran_order"]
            if (not isinstance(shape, tuple) or len(shape) != 2 or shape[1] != columns
                    or any(type(n) is not int or n < 1 for n in shape) or type(order) is not bool
                    or dtype.kind not in "iuf" or dtype.hasobject):
                raise ValueError("expected finite real numeric matrix")
        except (SyntaxError, ValueError, TypeError, KeyError, RecursionError) as exc:
            raise DataError(f"invalid NPY metadata: {exc}") from exc
        limits.check("max_input_samples", shape[0], "source" if source else "report")
        if source:
            AnalysisBudget(limits).source(shape[0], dtype.itemsize)
        else:
            limits.check("max_output_bytes", before[2], "report input")
        offset = file.tell()
        if offset + shape[0] * shape[1] * dtype.itemsize != before[2]:
            raise DataError("NPY payload length does not match its header")
        try:
            array = np.memmap(file, dtype=dtype, mode="r", offset=offset, shape=shape,
                              order="F" if order else "C")
            yield array, file
            if before != file_identity(os.fstat(file.fileno())) or path_before != file_identity(path.stat()):
                raise DataError("analysis source changed while being read")
        finally:
            if array is not None:
                array._mmap.close()


def hash_stream(file) -> str:
    file.seek(0)
    digest = sha256()
    while chunk := file.read(1024 * 1024):
        checkpoint()
        digest.update(chunk)
    return digest.hexdigest()


def load_waveform(path: Path, limits: AnalysisLimits):
    from wavebench.data.signal_pipeline import validate_waveform

    with mapped_npy(path, limits, columns=2, source=True) as (mapped, file):
        waveform = validate_waveform(mapped)
        digest = hash_stream(file)
    return waveform, digest
