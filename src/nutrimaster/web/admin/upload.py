"""Bounded, transactional extraction for the extraction-admin ZIP upload."""

from __future__ import annotations

import os
import struct
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterable


_MIB = 1024 * 1024
_COPY_CHUNK_BYTES = 1024 * 1024
_EOCD_SIGNATURE = b"PK\x05\x06"
_CENTRAL_DIRECTORY_SIGNATURE = b"PK\x01\x02"
_EOCD_SIZE = 22
_MAX_ZIP_COMMENT_BYTES = 65_535
_CENTRAL_DIRECTORY_HEADER_SIZE = 46
_MAX_OUTPUT_BASENAME_BYTES = 255


class ZipUploadError(ValueError):
    """An uploaded archive is malformed or exceeds a configured safety limit."""


class ZipUploadLimitError(ZipUploadError):
    """An otherwise parseable upload exceeds a hard resource limit."""


class ZipUploadStorageError(ZipUploadError):
    """The bounded upload could not be staged or committed on disk."""


def _bounded_env_int(name: str, default: int, *, hard_max: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not 1 <= value <= hard_max:
        raise RuntimeError(f"{name} must be between 1 and {hard_max}")
    return value


@dataclass(frozen=True)
class ZipUploadLimits:
    """Resource limits applied before and during recursive ZIP extraction."""

    max_archive_bytes: int
    max_extracted_bytes: int
    max_entry_bytes: int
    max_entries: int
    max_depth: int

    @classmethod
    def from_env(cls) -> "ZipUploadLimits":
        limits = cls(
            max_archive_bytes=_bounded_env_int(
                "NUTRIMASTER_ADMIN_ZIP_MAX_BYTES",
                32 * _MIB,
                hard_max=128 * _MIB,
            ),
            max_extracted_bytes=_bounded_env_int(
                "NUTRIMASTER_ADMIN_ZIP_MAX_EXTRACTED_BYTES",
                256 * _MIB,
                hard_max=512 * _MIB,
            ),
            max_entry_bytes=_bounded_env_int(
                "NUTRIMASTER_ADMIN_ZIP_MAX_ENTRY_BYTES",
                32 * _MIB,
                hard_max=64 * _MIB,
            ),
            max_entries=_bounded_env_int(
                "NUTRIMASTER_ADMIN_ZIP_MAX_ENTRIES",
                2_000,
                hard_max=10_000,
            ),
            max_depth=_bounded_env_int(
                "NUTRIMASTER_ADMIN_ZIP_MAX_DEPTH",
                4,
                hard_max=8,
            ),
        )
        if limits.max_entry_bytes > limits.max_extracted_bytes:
            raise RuntimeError(
                "NUTRIMASTER_ADMIN_ZIP_MAX_ENTRY_BYTES must not exceed "
                "NUTRIMASTER_ADMIN_ZIP_MAX_EXTRACTED_BYTES"
            )
        return limits


# Resolve at import time so an unsafe production setting prevents startup.
ZIP_UPLOAD_LIMITS = ZipUploadLimits.from_env()


@dataclass(frozen=True)
class ZipUploadResult:
    new_files: list[str]
    skipped_processed: list[str]
    skipped_existing: list[str]


@dataclass
class _ExtractionBudget:
    limits: ZipUploadLimits
    extracted_bytes: int = 0
    entries: int = 0

    @property
    def remaining_bytes(self) -> int:
        return self.limits.max_extracted_bytes - self.extracted_bytes

    def add_entries(self, count: int) -> None:
        if count < 0 or self.entries + count > self.limits.max_entries:
            raise ZipUploadLimitError(
                f"ZIP 条目数超过上限 {self.limits.max_entries}"
            )
        self.entries += count

    def validate_member_size(self, declared_size: int, name: str) -> None:
        if declared_size < 0:
            raise ZipUploadError(f"ZIP 条目大小无效: {name}")
        if declared_size > self.limits.max_entry_bytes:
            raise ZipUploadLimitError(
                f"ZIP 单条解压大小超过上限 {self.limits.max_entry_bytes} 字节: {name}"
            )
        if declared_size > self.remaining_bytes:
            raise ZipUploadLimitError(
                f"ZIP 总解压大小超过上限 {self.limits.max_extracted_bytes} 字节"
            )

    def consume(self, count: int, *, member_bytes: int, name: str) -> tuple[int, int]:
        next_member_bytes = member_bytes + count
        if next_member_bytes > self.limits.max_entry_bytes:
            raise ZipUploadLimitError(
                f"ZIP 单条解压大小超过上限 {self.limits.max_entry_bytes} 字节: {name}"
            )
        next_total = self.extracted_bytes + count
        if next_total > self.limits.max_extracted_bytes:
            raise ZipUploadLimitError(
                f"ZIP 总解压大小超过上限 {self.limits.max_extracted_bytes} 字节"
            )
        self.extracted_bytes = next_total
        return next_member_bytes, next_total


_upload_lock = threading.Lock()


def _copy_uploaded_archive(source: BinaryIO, destination: BinaryIO, limit: int) -> int:
    copied = 0
    while True:
        chunk = source.read(min(_COPY_CHUNK_BYTES, limit - copied + 1))
        if not chunk:
            break
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise ZipUploadError("上传流返回了无效数据")
        if copied + len(chunk) > limit:
            raise ZipUploadLimitError(f"ZIP 文件超过上传上限 {limit} 字节")
        destination.write(chunk)
        copied += len(chunk)
    if copied == 0:
        raise ZipUploadError("上传的 ZIP 文件为空")
    destination.flush()
    os.fsync(destination.fileno())
    return copied


def _find_eocd(archive_path: Path) -> tuple[int, tuple[int, ...]]:
    size = archive_path.stat().st_size
    tail_size = min(size, _EOCD_SIZE + _MAX_ZIP_COMMENT_BYTES)
    with archive_path.open("rb") as archive:
        archive.seek(size - tail_size)
        tail = archive.read(tail_size)

    search_before = len(tail)
    while True:
        position = tail.rfind(_EOCD_SIGNATURE, 0, search_before)
        if position < 0:
            raise ZipUploadError("上传内容不是有效的 ZIP 文件")
        if position + _EOCD_SIZE <= len(tail):
            values = struct.unpack_from("<4H2LH", tail, position + 4)
            comment_size = values[-1]
            if position + _EOCD_SIZE + comment_size == len(tail):
                return size - tail_size + position, values
        search_before = position


def _preflight_central_directory(archive_path: Path, remaining_entries: int) -> int:
    """Count central-directory records without allocating ``ZipInfo`` objects."""

    eocd_offset, values = _find_eocd(archive_path)
    (
        disk_number,
        central_directory_disk,
        entries_on_disk,
        declared_entries,
        central_directory_size,
        central_directory_offset,
        _comment_size,
    ) = values
    if disk_number or central_directory_disk or entries_on_disk != declared_entries:
        raise ZipUploadError("不支持分卷 ZIP 文件")
    if (
        declared_entries == 0xFFFF
        or central_directory_size == 0xFFFFFFFF
        or central_directory_offset == 0xFFFFFFFF
    ):
        raise ZipUploadError("不支持 ZIP64 上传")
    if declared_entries > remaining_entries:
        raise ZipUploadLimitError("ZIP 条目数超过上限")

    # ZIP permits bytes to precede the archive. Account for that prefix while
    # still requiring the central directory to end immediately before EOCD.
    prefix_size = eocd_offset - central_directory_size - central_directory_offset
    if prefix_size < 0:
        raise ZipUploadError("ZIP 中央目录偏移无效")
    actual_offset = prefix_size + central_directory_offset
    consumed = 0
    counted = 0
    with archive_path.open("rb") as archive:
        archive.seek(actual_offset)
        while consumed < central_directory_size:
            header = archive.read(_CENTRAL_DIRECTORY_HEADER_SIZE)
            if len(header) != _CENTRAL_DIRECTORY_HEADER_SIZE:
                raise ZipUploadError("ZIP 中央目录不完整")
            if header[:4] != _CENTRAL_DIRECTORY_SIGNATURE:
                raise ZipUploadError("ZIP 中央目录格式无效")
            filename_size, extra_size, comment_size = struct.unpack_from(
                "<3H", header, 28
            )
            record_size = (
                _CENTRAL_DIRECTORY_HEADER_SIZE
                + filename_size
                + extra_size
                + comment_size
            )
            if record_size > central_directory_size - consumed:
                raise ZipUploadError("ZIP 中央目录条目越界")
            archive.seek(record_size - _CENTRAL_DIRECTORY_HEADER_SIZE, os.SEEK_CUR)
            consumed += record_size
            counted += 1
            if counted > remaining_entries:
                raise ZipUploadLimitError("ZIP 条目数超过上限")

    if consumed != central_directory_size or counted != declared_entries:
        raise ZipUploadError("ZIP 中央目录条目数不一致")
    return counted


def _safe_basename(archive_name: str) -> str:
    basename = archive_name.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    if basename in {"", ".", ".."} or "\x00" in basename:
        raise ZipUploadError("ZIP 包含无效文件名")
    if len(os.fsencode(basename)) > _MAX_OUTPUT_BASENAME_BYTES:
        raise ZipUploadError(f"ZIP 文件名过长: {basename[:80]}")
    return basename


def _copy_zip_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    destination: Path,
    budget: _ExtractionBudget,
) -> None:
    if info.flag_bits & 0x1:
        raise ZipUploadError(f"不支持加密 ZIP 条目: {info.filename}")
    budget.validate_member_size(info.file_size, info.filename)
    member_bytes = 0
    try:
        with archive.open(info, "r") as source, destination.open("xb") as target:
            while True:
                chunk = source.read(
                    min(
                        _COPY_CHUNK_BYTES,
                        budget.limits.max_entry_bytes - member_bytes + 1,
                        budget.remaining_bytes + 1,
                    )
                )
                if not chunk:
                    break
                member_bytes, _ = budget.consume(
                    len(chunk), member_bytes=member_bytes, name=info.filename
                )
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
    except ZipUploadError:
        destination.unlink(missing_ok=True)
        raise
    except OSError as exc:
        destination.unlink(missing_ok=True)
        raise ZipUploadStorageError(
            f"无法暂存 ZIP 条目 {info.filename}: {exc}"
        ) from exc
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
        destination.unlink(missing_ok=True)
        raise ZipUploadError(f"无法解压 ZIP 条目 {info.filename}: {exc}") from exc
    if member_bytes != info.file_size:
        destination.unlink(missing_ok=True)
        raise ZipUploadError(f"ZIP 条目大小与目录不一致: {info.filename}")


def _is_macos_metadata(name: str) -> bool:
    normalized = name.replace("\\", "/").lstrip("/")
    return normalized == "__MACOSX" or normalized.startswith("__MACOSX/")


def _commit_staged_files(
    staged_files: Iterable[tuple[str, Path]],
    input_dir: Path,
    skipped_existing: list[str],
) -> list[str]:
    committed: list[Path] = []
    committed_names: list[str] = []
    active_destination: Path | None = None
    try:
        for basename, staged_path in staged_files:
            destination = input_dir / basename
            try:
                # The staging directory is deliberately inside input_dir, so
                # hard-link creation is an atomic, no-overwrite commit.
                os.link(staged_path, destination)
            except FileExistsError:
                skipped_existing.append(basename)
                continue
            active_destination = destination
            committed.append(destination)
            committed_names.append(basename)
            staged_path.unlink()
            active_destination = None
    except BaseException as exc:
        if active_destination is not None:
            active_destination.unlink(missing_ok=True)
        for destination in reversed(committed):
            destination.unlink(missing_ok=True)
        if isinstance(exc, OSError):
            raise ZipUploadStorageError(f"无法原子写入上传文件: {exc}") from exc
        raise
    return committed_names


def extract_zip_upload(
    source: BinaryIO,
    *,
    input_dir: Path,
    processed_stems: Iterable[str],
    existing_stems: Iterable[str],
    limits: ZipUploadLimits = ZIP_UPLOAD_LIMITS,
) -> ZipUploadResult:
    """Extract relevant members with bounded memory/disk and atomic outputs."""

    input_dir = Path(input_dir)
    input_dir.mkdir(parents=True, exist_ok=True)
    processed = set(processed_stems)

    with _upload_lock:
        existing = set(existing_stems)
        existing.update(path.stem for path in input_dir.glob("*.md"))
        skipped_processed: list[str] = []
        skipped_existing: list[str] = []
        staged_files: list[tuple[str, Path]] = []
        budget = _ExtractionBudget(limits)
        archive_sequence = 0
        markdown_sequence = 0

        with tempfile.TemporaryDirectory(
            dir=input_dir, prefix=".admin-upload-"
        ) as temporary_directory:
            temporary_root = Path(temporary_directory)
            top_archive = temporary_root / "archive-0.zip"
            with top_archive.open("xb") as destination:
                _copy_uploaded_archive(source, destination, limits.max_archive_bytes)

            def extract_archive(archive_path: Path, depth: int) -> None:
                nonlocal archive_sequence, markdown_sequence
                if depth > limits.max_depth:
                    raise ZipUploadLimitError(
                        f"嵌套 ZIP 深度超过上限 {limits.max_depth}"
                    )
                remaining_entries = limits.max_entries - budget.entries
                entry_count = _preflight_central_directory(
                    archive_path, remaining_entries
                )
                budget.add_entries(entry_count)
                try:
                    archive = zipfile.ZipFile(archive_path, "r")
                except (zipfile.BadZipFile, OSError) as exc:
                    raise ZipUploadError(f"上传内容不是有效的 ZIP 文件: {exc}") from exc

                with archive:
                    infos = archive.infolist()
                    if len(infos) != entry_count:
                        raise ZipUploadError("ZIP 中央目录条目数不一致")
                    for info in infos:
                        if info.is_dir() or _is_macos_metadata(info.filename):
                            continue
                        normalized_name = info.filename.replace("\\", "/")
                        suffix = Path(normalized_name).suffix.lower()
                        if suffix == ".zip":
                            if depth >= limits.max_depth:
                                raise ZipUploadLimitError(
                                    f"嵌套 ZIP 深度超过上限 {limits.max_depth}"
                                )
                            archive_sequence += 1
                            nested_path = temporary_root / f"archive-{archive_sequence}.zip"
                            _copy_zip_member(archive, info, nested_path, budget)
                            try:
                                extract_archive(nested_path, depth + 1)
                            finally:
                                nested_path.unlink(missing_ok=True)
                        elif suffix == ".md":
                            basename = _safe_basename(info.filename)
                            stem = Path(basename).stem
                            if stem in processed:
                                skipped_processed.append(basename)
                                continue
                            if stem in existing:
                                skipped_existing.append(basename)
                                continue
                            markdown_sequence += 1
                            staged_path = temporary_root / f"markdown-{markdown_sequence}.part"
                            _copy_zip_member(archive, info, staged_path, budget)
                            staged_files.append((basename, staged_path))
                            existing.add(stem)

            extract_archive(top_archive, 0)
            new_files = _commit_staged_files(
                staged_files, input_dir, skipped_existing
            )

        return ZipUploadResult(
            new_files=new_files,
            skipped_processed=skipped_processed,
            skipped_existing=skipped_existing,
        )
