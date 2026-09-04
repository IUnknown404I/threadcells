"""Persistent bounded random access for immutable gzip terminal history."""

from __future__ import annotations

import fcntl
import gzip
import os
import secrets
import stat
import struct
import threading
import zlib
from dataclasses import dataclass
from pathlib import Path

CHUNK_BYTES = 256 * 1024
MAX_INDEXED_RAW_BYTES = 64 * 1024 * 1024 * 1024
_MAGIC = b"TCGZIDX1"
_VERSION = 1
_HEADER = struct.Struct(">8sBQQQQQII")
_ENTRY = struct.Struct(">QQII")
_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


def _lock_for(terminal_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(terminal_id, threading.Lock())


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short write while building compressed output index")
        view = view[written:]


@dataclass
class CompressedOutputIndex:
    """Two regular-file descriptors bound to one exact gzip source."""

    index_descriptor: int
    data_descriptor: int
    raw_size: int
    chunk_count: int

    def close(self) -> None:
        os.close(self.index_descriptor)
        os.close(self.data_descriptor)

    def _entry(self, number: int) -> tuple[int, int, int, int]:
        if not 0 <= number < self.chunk_count:
            raise ValueError("compressed output chunk is outside the index")
        payload = os.pread(self.index_descriptor, _ENTRY.size, _HEADER.size + number * _ENTRY.size)
        if len(payload) != _ENTRY.size:
            raise OSError("compressed output index entry is incomplete")
        raw_start, data_start, compressed_size, raw_size = _ENTRY.unpack(payload)
        if (
            raw_start != number * CHUNK_BYTES
            or not 0 < raw_size <= CHUNK_BYTES
            or compressed_size <= 0
            or compressed_size > CHUNK_BYTES + 4096
        ):
            raise OSError("compressed output index entry is invalid")
        return raw_start, data_start, compressed_size, raw_size

    def read(self, start: int, length: int) -> bytes:
        """Read at most two page widths without inflating unrelated history."""
        if start < 0 or length < 0 or start + length > self.raw_size:
            raise ValueError("compressed output range is invalid")
        if length > 1024 * 1024 + 8:
            raise ValueError("compressed output range exceeds the bounded reader")
        if length == 0:
            return b""
        first = start // CHUNK_BYTES
        last = (start + length - 1) // CHUNK_BYTES
        output = bytearray()
        for number in range(first, last + 1):
            raw_start, data_start, compressed_size, raw_size = self._entry(number)
            compressed = os.pread(self.data_descriptor, compressed_size, data_start)
            if len(compressed) != compressed_size:
                raise OSError("compressed output data is incomplete")
            inflater = zlib.decompressobj()
            raw = inflater.decompress(compressed, raw_size + 1)
            raw += inflater.flush()
            if len(raw) != raw_size or not inflater.eof or inflater.unused_data:
                raise OSError("compressed output data failed validation")
            left = max(start, raw_start) - raw_start
            right = min(start + length, raw_start + raw_size) - raw_start
            output.extend(raw[left:right])
        if len(output) != length:
            raise OSError("compressed output index returned a short range")
        return bytes(output)


def _source_identity(source: os.stat_result) -> tuple[int, int, int, int]:
    return source.st_dev, source.st_ino, source.st_size, source.st_mtime_ns


def _open_regular(directory_fd: int, name: str) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(name, flags, dir_fd=directory_fd)
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        os.close(descriptor)
        raise OSError("compressed output index is not a regular file")
    return descriptor, metadata


def _open_valid(
    directory_fd: int,
    terminal_id: str,
    source: os.stat_result,
) -> CompressedOutputIndex | None:
    index_descriptor = data_descriptor = -1
    try:
        index_descriptor, index_stat = _open_regular(directory_fd, f"{terminal_id}.log.tci")
        data_descriptor, data_stat = _open_regular(directory_fd, f"{terminal_id}.log.tcd")
        payload = os.pread(index_descriptor, _HEADER.size, 0)
        if len(payload) != _HEADER.size:
            return None
        (
            magic,
            version,
            device,
            inode,
            source_size,
            source_mtime_ns,
            raw_size,
            chunk_size,
            chunk_count,
        ) = _HEADER.unpack(payload)
        if (
            magic != _MAGIC
            or version != _VERSION
            or (device, inode, source_size, source_mtime_ns) != _source_identity(source)
            or chunk_size != CHUNK_BYTES
            or raw_size > MAX_INDEXED_RAW_BYTES
            or chunk_count != ((raw_size + CHUNK_BYTES - 1) // CHUNK_BYTES if raw_size else 0)
            or index_stat.st_size != _HEADER.size + chunk_count * _ENTRY.size
        ):
            return None
        if chunk_count:
            last_payload = os.pread(
                index_descriptor,
                _ENTRY.size,
                _HEADER.size + (chunk_count - 1) * _ENTRY.size,
            )
            if len(last_payload) != _ENTRY.size:
                return None
            _raw_start, data_start, compressed_size, _raw_chunk_size = _ENTRY.unpack(last_payload)
            if data_start + compressed_size != data_stat.st_size:
                return None
        elif data_stat.st_size != 0:
            return None
        opened = CompressedOutputIndex(index_descriptor, data_descriptor, raw_size, chunk_count)
        index_descriptor = data_descriptor = -1
        return opened
    except (FileNotFoundError, OSError, struct.error):
        return None
    finally:
        if index_descriptor >= 0:
            os.close(index_descriptor)
        if data_descriptor >= 0:
            os.close(data_descriptor)


def _build(
    directory_fd: int,
    terminal_id: str,
    source_descriptor: int,
    source: os.stat_result,
) -> None:
    token = secrets.token_hex(8)
    index_temp = f".{terminal_id}.{token}.tci"
    data_temp = f".{terminal_id}.{token}.tcd"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    index_descriptor = data_descriptor = -1
    try:
        index_descriptor = os.open(index_temp, flags, 0o600, dir_fd=directory_fd)
        data_descriptor = os.open(data_temp, flags, 0o600, dir_fd=directory_fd)
        _write_all(index_descriptor, bytes(_HEADER.size))
        duplicate = os.dup(source_descriptor)
        raw_offset = data_offset = chunk_count = 0
        with os.fdopen(duplicate, "rb") as raw_source, gzip.GzipFile(fileobj=raw_source) as stream:
            while True:
                raw = stream.read(CHUNK_BYTES)
                if not raw:
                    break
                if raw_offset + len(raw) > MAX_INDEXED_RAW_BYTES:
                    raise OSError("compressed terminal output exceeds the indexing ceiling")
                compressed = zlib.compress(raw, level=1)
                _write_all(data_descriptor, compressed)
                _write_all(
                    index_descriptor,
                    _ENTRY.pack(raw_offset, data_offset, len(compressed), len(raw)),
                )
                raw_offset += len(raw)
                data_offset += len(compressed)
                chunk_count += 1
        if _source_identity(os.fstat(source_descriptor)) != _source_identity(source):
            raise OSError("compressed terminal output changed while indexing")
        header = _HEADER.pack(
            _MAGIC,
            _VERSION,
            source.st_dev,
            source.st_ino,
            source.st_size,
            source.st_mtime_ns,
            raw_offset,
            CHUNK_BYTES,
            chunk_count,
        )
        if os.pwrite(index_descriptor, header, 0) != len(header):
            raise OSError("compressed output index header write was incomplete")
        os.fsync(data_descriptor)
        os.fsync(index_descriptor)
        os.close(data_descriptor)
        data_descriptor = -1
        os.close(index_descriptor)
        index_descriptor = -1
        # The index is the publication boundary. Rebuilding the data first is
        # safe because every generation is deterministic and bound to the same
        # exact immutable source identity.
        os.replace(
            data_temp, f"{terminal_id}.log.tcd", src_dir_fd=directory_fd, dst_dir_fd=directory_fd
        )
        os.replace(
            index_temp, f"{terminal_id}.log.tci", src_dir_fd=directory_fd, dst_dir_fd=directory_fd
        )
        os.fsync(directory_fd)
    finally:
        if data_descriptor >= 0:
            os.close(data_descriptor)
        if index_descriptor >= 0:
            os.close(index_descriptor)
        for name in (data_temp, index_temp):
            try:
                os.unlink(name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass


def open_compressed_output_index(
    directory: Path,
    terminal_id: str,
    source_descriptor: int,
    source: os.stat_result,
) -> CompressedOutputIndex:
    """Open or create the exact source's persistent page index."""
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    with _lock_for(terminal_id):
        directory_fd = os.open(directory, directory_flags)
        lock_descriptor = -1
        try:
            lock_flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
            if hasattr(os, "O_NOFOLLOW"):
                lock_flags |= os.O_NOFOLLOW
            lock_descriptor = os.open(
                f"{terminal_id}.log.output-index.lock",
                lock_flags,
                0o600,
                dir_fd=directory_fd,
            )
            if not stat.S_ISREG(os.fstat(lock_descriptor).st_mode):
                raise OSError("compressed output index lock is not a regular file")
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
            opened = _open_valid(directory_fd, terminal_id, source)
            if opened is not None:
                return opened
            _build(directory_fd, terminal_id, source_descriptor, source)
            opened = _open_valid(directory_fd, terminal_id, source)
            if opened is None:
                raise OSError("compressed output index publication failed")
            return opened
        finally:
            if lock_descriptor >= 0:
                os.close(lock_descriptor)
            os.close(directory_fd)
