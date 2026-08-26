from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path

import pytest
from flask import Flask

from nutrimaster.web.admin.upload import (
    ZipUploadError,
    ZipUploadLimitError,
    ZipUploadLimits,
    extract_zip_upload,
)


def _zip_bytes(entries: list[tuple[str, bytes]], *, compression=zipfile.ZIP_DEFLATED) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as archive:
        for name, payload in entries:
            archive.writestr(name, payload)
    return output.getvalue()


def _limits(**overrides: int) -> ZipUploadLimits:
    values = {
        "max_archive_bytes": 1024 * 1024,
        "max_extracted_bytes": 1024 * 1024,
        "max_entry_bytes": 512 * 1024,
        "max_entries": 100,
        "max_depth": 4,
    }
    values.update(overrides)
    return ZipUploadLimits(**values)


def test_nested_zip_preserves_dedup_and_uses_only_safe_basenames(tmp_path: Path):
    inner = _zip_bytes(
        [
            ("../../fresh.md", b"fresh"),
            ("folder/already.md", b"duplicate"),
            ("folder/processed.md", b"processed"),
        ]
    )
    outer = _zip_bytes(
        [
            ("nested/archive.zip", inner),
            ("windows\\second.md", b"second"),
            ("__MACOSX/ignored.md", b"metadata"),
            ("notes.txt", b"ignored"),
        ]
    )
    (tmp_path / "already.md").write_bytes(b"original")

    result = extract_zip_upload(
        io.BytesIO(outer),
        input_dir=tmp_path,
        processed_stems={"processed"},
        existing_stems={"already"},
        limits=_limits(),
    )

    assert result.new_files == ["fresh.md", "second.md"]
    assert result.skipped_existing == ["already.md"]
    assert result.skipped_processed == ["processed.md"]
    assert (tmp_path / "fresh.md").read_bytes() == b"fresh"
    assert (tmp_path / "second.md").read_bytes() == b"second"
    assert (tmp_path / "already.md").read_bytes() == b"original"
    assert not (tmp_path.parent / "fresh.md").exists()
    assert not list(tmp_path.glob(".admin-upload-*"))


class _NoUnboundedRead(io.BytesIO):
    def __init__(self, payload: bytes):
        super().__init__(payload)
        self.request_sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        assert size >= 0, "the upload stream must never be read without a bound"
        self.request_sizes.append(size)
        return super().read(size)


def test_uploaded_archive_is_copied_in_bounded_chunks(tmp_path: Path):
    source = _NoUnboundedRead(_zip_bytes([("paper.md", b"content")]))

    result = extract_zip_upload(
        source,
        input_dir=tmp_path,
        processed_stems=set(),
        existing_stems=set(),
        limits=_limits(max_archive_bytes=128),
    )

    assert result.new_files == ["paper.md"]
    assert source.request_sizes
    assert max(source.request_sizes) <= 129


def test_compressed_upload_limit_rejects_before_extracting(tmp_path: Path):
    payload = _zip_bytes([("paper.md", os.urandom(512))], compression=zipfile.ZIP_STORED)

    with pytest.raises(ZipUploadError, match="上传上限"):
        extract_zip_upload(
            io.BytesIO(payload),
            input_dir=tmp_path,
            processed_stems=set(),
            existing_stems=set(),
            limits=_limits(max_archive_bytes=len(payload) - 1),
        )

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("limit_overrides", "message"),
    [
        ({"max_entry_bytes": 64}, "单条解压大小"),
        ({"max_extracted_bytes": 64}, "总解压大小"),
    ],
)
def test_decompression_limits_leave_no_partial_markdown(
    tmp_path: Path,
    limit_overrides: dict[str, int],
    message: str,
):
    # Highly compressible data models a ZIP bomb while keeping the request tiny.
    payload = _zip_bytes([("first.md", b"safe"), ("bomb.md", b"x" * 65)])

    with pytest.raises(ZipUploadError, match=message):
        extract_zip_upload(
            io.BytesIO(payload),
            input_dir=tmp_path,
            processed_stems=set(),
            existing_stems=set(),
            limits=_limits(**limit_overrides),
        )

    assert list(tmp_path.iterdir()) == []


def test_recursive_entry_count_and_depth_are_hard_limits(tmp_path: Path):
    inner = _zip_bytes([("one.md", b"1"), ("two.md", b"2")])
    outer = _zip_bytes([("nested.zip", inner)])

    with pytest.raises(ZipUploadError, match="条目数"):
        extract_zip_upload(
            io.BytesIO(outer),
            input_dir=tmp_path,
            processed_stems=set(),
            existing_stems=set(),
            limits=_limits(max_entries=2),
        )

    assert list(tmp_path.iterdir()) == []

    deepest = _zip_bytes([("paper.md", b"paper")])
    middle = _zip_bytes([("deepest.zip", deepest)])
    depth_limited_outer = _zip_bytes([("middle.zip", middle)])
    with pytest.raises(ZipUploadError, match="深度"):
        extract_zip_upload(
            io.BytesIO(depth_limited_outer),
            input_dir=tmp_path,
            processed_stems=set(),
            existing_stems=set(),
            limits=_limits(max_depth=1),
        )

    assert list(tmp_path.iterdir()) == []


def test_bad_nested_zip_rolls_back_files_staged_before_it(tmp_path: Path):
    outer = _zip_bytes([("first.md", b"safe"), ("broken.zip", b"not a zip")])

    with pytest.raises(ZipUploadError, match="有效的 ZIP"):
        extract_zip_upload(
            io.BytesIO(outer),
            input_dir=tmp_path,
            processed_stems=set(),
            existing_stems=set(),
            limits=_limits(),
        )

    assert list(tmp_path.iterdir()) == []


def test_atomic_commit_rolls_back_if_a_later_link_fails(tmp_path: Path, monkeypatch):
    import nutrimaster.web.admin.upload as upload_module

    payload = _zip_bytes([("one.md", b"1"), ("two.md", b"2")])
    real_link = upload_module.os.link
    calls = 0

    def fail_second_link(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated disk failure")
        return real_link(source, destination)

    monkeypatch.setattr(upload_module.os, "link", fail_second_link)
    with pytest.raises(ZipUploadError, match="原子写入"):
        extract_zip_upload(
            io.BytesIO(payload),
            input_dir=tmp_path,
            processed_stems=set(),
            existing_stems=set(),
            limits=_limits(),
        )

    assert list(tmp_path.iterdir()) == []


def test_upload_limit_environment_has_safe_defaults_and_hard_caps(monkeypatch):
    names = [
        "NUTRIMASTER_ADMIN_ZIP_MAX_BYTES",
        "NUTRIMASTER_ADMIN_ZIP_MAX_EXTRACTED_BYTES",
        "NUTRIMASTER_ADMIN_ZIP_MAX_ENTRY_BYTES",
        "NUTRIMASTER_ADMIN_ZIP_MAX_ENTRIES",
        "NUTRIMASTER_ADMIN_ZIP_MAX_DEPTH",
    ]
    for name in names:
        monkeypatch.delenv(name, raising=False)

    limits = ZipUploadLimits.from_env()
    assert limits == ZipUploadLimits(
        max_archive_bytes=32 * 1024 * 1024,
        max_extracted_bytes=256 * 1024 * 1024,
        max_entry_bytes=32 * 1024 * 1024,
        max_entries=2_000,
        max_depth=4,
    )

    monkeypatch.setenv("NUTRIMASTER_ADMIN_ZIP_MAX_DEPTH", "9")
    with pytest.raises(RuntimeError, match="between 1 and 8"):
        ZipUploadLimits.from_env()
    monkeypatch.setenv("NUTRIMASTER_ADMIN_ZIP_MAX_DEPTH", "invalid")
    with pytest.raises(RuntimeError, match="must be an integer"):
        ZipUploadLimits.from_env()


def test_admin_route_delegates_to_streaming_extractor():
    source = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "nutrimaster"
        / "web"
        / "admin"
        / "app.py"
    ).read_text(encoding="utf-8")
    upload_route = source[source.index("def api_upload():") : source.index(
        "# ╔", source.index("def api_upload():")
    )]

    assert "extract_zip_upload(" in upload_route
    assert "f.stream" in upload_route
    assert "f.read(" not in upload_route
    assert "BytesIO" not in upload_route
    assert "zf.read(" not in upload_route


def test_admin_route_returns_413_for_zip_limits_and_does_not_swallow_memory_error(
    monkeypatch,
):
    import nutrimaster.web.admin.app as admin_app

    flask_app = Flask(__name__)
    monkeypatch.setattr(admin_app, "ensure_dirs", lambda: None)
    monkeypatch.setattr(admin_app, "get_processed_stems", lambda: set())
    monkeypatch.setattr(admin_app, "get_input_files", lambda: [])

    def raise_limit(*_args, **_kwargs):
        raise ZipUploadLimitError("too large")

    monkeypatch.setattr(admin_app, "extract_zip_upload", raise_limit)
    with flask_app.test_request_context(
        "/api/upload",
        method="POST",
        data={"file": (io.BytesIO(b"zip"), "papers.zip")},
    ):
        response, status = admin_app.api_upload.__wrapped__()
    assert status == 413
    assert response.get_json() == {"error": "解压失败: too large"}

    def raise_memory_error(*_args, **_kwargs):
        raise MemoryError("simulated")

    monkeypatch.setattr(admin_app, "extract_zip_upload", raise_memory_error)
    with flask_app.test_request_context(
        "/api/upload",
        method="POST",
        data={"file": (io.BytesIO(b"zip"), "papers.zip")},
    ), pytest.raises(MemoryError, match="simulated"):
        admin_app.api_upload.__wrapped__()
