#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


RELEASE_FORMAT = "nutrimaster-release-v1"
EXPECTED_MODEL = "deepseek-v4-flash"
MAX_SOURCE_FILE_BYTES = 64 * 1024 * 1024
MAX_SOURCE_TOTAL_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 20_000

ROOT = Path(__file__).resolve().parents[1]

_EXACT_FILES = (
    ".env.example",
    "pyproject.toml",
    "uv.lock",
    "start-unified-production.sh",
    "start-pi-production.sh",
    "start-index-builder-production.sh",
    "start-local.sh",
    "DEPLOYMENT_CHECKLIST.md",
    "DEPLOYMENT_GUIDE.md",
    "docs/index_builder_operations.md",
    "docs/pi_runtime_migration.md",
    "docs/production_release_runbook.md",
    "pi-runtime/.nvmrc",
    "pi-runtime/package.json",
    "pi-runtime/package-lock.json",
    "pi-runtime/README.md",
    "deploy/build_release.py",
    "deploy/preflight_release.py",
    "deploy/migrate_production_gateway.py",
    "deploy/smoke_production.py",
    "deploy/stage-production.sh",
    "deploy/bootstrap-production.sh",
    "deploy/cutover-production.sh",
    "deploy/rollback-production.sh",
)
_TREE_ROOTS = (
    "src/nutrimaster",
    "pi-runtime/src",
    "deploy/systemd",
    "deploy/nginx",
)
_EXCLUDED_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    ".pi-agent",
    "reports",
    "output",
    "processed",
}
_EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".so", ".log", ".tmp", ".swp"}
_FORBIDDEN_NAMES = {
    ".env",
    "auth.json",
    "id_rsa",
    "id_ed25519",
}
_FORBIDDEN_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}
_SECRET_ASSIGNMENT_RE = re.compile(
    rb"(?m)^\s*(?:export\s+)?"
    rb"(OPENAI_API_KEY|JINA_API_KEY|SUPABASE_SERVICE_ROLE_KEY|"
    rb"SUPABASE_ANON_KEY|JWT_SECRET|CLIENT_SECRET|PRIVATE_KEY)"
    rb"\s*=\s*([^\r\n#]+)"
)
_TOKEN_RE = re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b")


@dataclass(frozen=True)
class SourceFile:
    path: str
    source: Path
    size: int
    mode: int
    sha256: str

    def as_manifest(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "size": self.size,
            "mode": f"{self.mode:04o}",
            "sha256": self.sha256,
        }


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path, *, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        while block := file.read(block_size):
            digest.update(block)
    return digest.hexdigest()


def _safe_relative_path(path: str) -> PurePosixPath:
    candidate = PurePosixPath(path)
    if (
        not path
        or candidate.is_absolute()
        or ".." in candidate.parts
        or "." in candidate.parts
        or "" in candidate.parts
    ):
        raise RuntimeError(f"unsafe release path: {path!r}")
    return candidate


def _path_is_excluded(relative: Path) -> bool:
    if any(part in _EXCLUDED_PARTS for part in relative.parts):
        return True
    if relative.suffix.lower() in _EXCLUDED_SUFFIXES:
        return True
    return False


def _assert_safe_source_path(relative: Path) -> None:
    _safe_relative_path(relative.as_posix())
    if relative.name in _FORBIDDEN_NAMES and relative.as_posix() != ".env.example":
        raise RuntimeError(f"secret-bearing path is forbidden in a release: {relative}")
    if relative.suffix.lower() in _FORBIDDEN_SUFFIXES:
        raise RuntimeError(f"private key material is forbidden in a release: {relative}")


def _assert_no_secret_content(relative: str, data: bytes) -> None:
    # Runtime assets larger than four MiB are binary/static resources. They
    # still receive the token scan but skip line-oriented environment parsing.
    if _TOKEN_RE.search(data):
        raise RuntimeError(f"possible API token found in release source: {relative}")
    relative_path = PurePosixPath(relative)
    if len(data) > 4 * 1024 * 1024 or not (
        relative_path.name.startswith(".env")
        or relative_path.suffix.lower() in {".sh", ".md", ".txt"}
    ):
        return
    for match in _SECRET_ASSIGNMENT_RE.finditer(data):
        value = match.group(2).strip().strip(b"'\"")
        lowered = value.lower()
        if (
            not value
            or value == b"..."
            or lowered.startswith((b"test", b"your", b"replace", b"example"))
            or value.startswith((b"<", b"${"))
        ):
            continue
        key = match.group(1).decode("ascii", errors="replace")
        raise RuntimeError(f"non-placeholder {key} found in release source: {relative}")


def _iter_tree_files(root: Path, relative_root: Path) -> Iterable[Path]:
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError(f"release source tree is missing or unsafe: {relative_root}")
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(ROOT)
        if _path_is_excluded(relative):
            continue
        if path.is_symlink():
            raise RuntimeError(f"symlink is forbidden in release sources: {relative}")
        if path.is_file():
            yield path


def collect_source_files() -> list[SourceFile]:
    paths: dict[str, Path] = {}
    for relative_text in _EXACT_FILES:
        relative = Path(relative_text)
        source = ROOT / relative
        if source.is_symlink() or not source.is_file():
            raise RuntimeError(f"required release source is missing or unsafe: {relative}")
        paths[relative.as_posix()] = source
    for relative_text in _TREE_ROOTS:
        relative_root = Path(relative_text)
        for source in _iter_tree_files(ROOT / relative_root, relative_root):
            relative = source.relative_to(ROOT)
            paths[relative.as_posix()] = source

    files: list[SourceFile] = []
    total = 0
    for relative_text, source in sorted(paths.items()):
        relative = Path(relative_text)
        _assert_safe_source_path(relative)
        size = source.stat().st_size
        if size > MAX_SOURCE_FILE_BYTES:
            raise RuntimeError(
                f"release source exceeds {MAX_SOURCE_FILE_BYTES} bytes: {relative}"
            )
        data = source.read_bytes()
        if len(data) != size:
            raise RuntimeError(f"release source changed while it was read: {relative}")
        _assert_no_secret_content(relative_text, data)
        total += size
        if total > MAX_SOURCE_TOTAL_BYTES:
            raise RuntimeError(
                f"release sources exceed {MAX_SOURCE_TOTAL_BYTES} total bytes"
            )
        mode = 0o755 if os.access(source, os.X_OK) else 0o644
        files.append(
            SourceFile(
                path=relative_text,
                source=source,
                size=size,
                mode=mode,
                sha256=_sha256_bytes(data),
            )
        )
    return files


def _parse_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _assert_model_contract() -> None:
    example = _parse_dotenv(ROOT / ".env.example")
    if example.get("MAIN_MODEL") != EXPECTED_MODEL:
        raise RuntimeError(
            f".env.example MAIN_MODEL must remain exactly {EXPECTED_MODEL}"
        )
    pi_script = (ROOT / "start-pi-production.sh").read_text(encoding="utf-8")
    if (
        f'EXPECTED_MODEL="{EXPECTED_MODEL}"' not in pi_script
        or 'export NUTRIMASTER_PI_MODEL="${NUTRIMASTER_PI_MODEL:-$EXPECTED_MODEL}"'
        not in pi_script
        or '"${MAIN_MODEL:-}" != "$EXPECTED_MODEL"' not in pi_script
    ):
        raise RuntimeError("Pi production model fallback is not locked to deepseek-v4-flash")


def _git_output(arguments: list[str]) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout).strip())
    return completed.stdout


def _source_provenance(files: list[SourceFile]) -> dict[str, Any]:
    selected = {item.path for item in files}
    tracked_changes = {
        line
        for line in _git_output(["diff", "--name-only", "--relative", "--"]).splitlines()
        if line
    }
    untracked = {
        line
        for line in _git_output(
            ["ls-files", "--others", "--exclude-standard"]
        ).splitlines()
        if line
    }
    return {
        "git_head": _git_output(["rev-parse", "HEAD"]).strip(),
        "dirty_selected_paths": sorted(selected & tracked_changes),
        "untracked_selected_paths": sorted(selected & untracked),
    }


def _source_digest(files: list[SourceFile]) -> str:
    canonical = "".join(
        f"{item.path}\0{item.mode:o}\0{item.size}\0{item.sha256}\n" for item in files
    ).encode("utf-8")
    return _sha256_bytes(canonical)


def _tar_info(name: str, *, size: int, mode: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name=name)
    info.size = size
    info.mode = mode
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    return info


def _add_bytes(archive: tarfile.TarFile, name: str, data: bytes, mode: int) -> None:
    archive.addfile(_tar_info(name, size=len(data), mode=mode), io.BytesIO(data))


def build_release(output_dir: Path) -> dict[str, Any]:
    _assert_model_contract()
    files = collect_source_files()
    digest = _source_digest(files)
    release_id = digest[:20]
    prefix = f"nutrimaster-release-{release_id}"
    provenance = _source_provenance(files)
    manifest = {
        "format": RELEASE_FORMAT,
        "release_id": release_id,
        "source_digest": digest,
        "expected_model": EXPECTED_MODEL,
        "git_head": provenance["git_head"],
        "dirty_selected_paths": provenance["dirty_selected_paths"],
        "untracked_selected_paths": provenance["untracked_selected_paths"],
        "files": [item.as_manifest() for item in files],
    }
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )
    sums = "".join(f"{item.sha256}  {item.path}\n" for item in files).encode("utf-8")

    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{prefix}.tar.gz"
    sidecar = output.with_suffix(output.suffix + ".sha256")
    if output.exists() or sidecar.exists():
        raise RuntimeError(f"refusing to overwrite an existing release artifact: {output}")

    with output.open("xb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(mode="w", fileobj=compressed, format=tarfile.PAX_FORMAT) as archive:
                for item in files:
                    data = item.source.read_bytes()
                    if len(data) != item.size or _sha256_bytes(data) != item.sha256:
                        raise RuntimeError(
                            f"release source changed after admission: {item.path}"
                        )
                    _add_bytes(archive, f"{prefix}/{item.path}", data, item.mode)
                _add_bytes(archive, f"{prefix}/RELEASE.json", manifest_bytes, 0o644)
                _add_bytes(archive, f"{prefix}/SHA256SUMS", sums, 0o644)

    if output.stat().st_size > MAX_ARCHIVE_BYTES:
        raise RuntimeError(f"release archive exceeds {MAX_ARCHIVE_BYTES} bytes: {output}")
    archive_digest = _sha256_file(output)
    sidecar.write_text(f"{archive_digest}  {output.name}\n", encoding="ascii")
    verified = verify_release(output)
    return {
        **verified,
        "archive": str(output),
        "sidecar": str(sidecar),
        "archive_sha256": archive_digest,
        "archive_bytes": output.stat().st_size,
    }


def _read_member(archive: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    extracted = archive.extractfile(member)
    if extracted is None:
        raise RuntimeError(f"release member is unreadable: {member.name}")
    data = extracted.read(MAX_SOURCE_TOTAL_BYTES + 1)
    if len(data) != member.size:
        raise RuntimeError(f"release member size changed: {member.name}")
    return data


def verify_release(path: Path) -> dict[str, Any]:
    path = Path(path).resolve()
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"release archive is missing or unsafe: {path}")
    if path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise RuntimeError("release archive is too large")
    sidecar = path.with_suffix(path.suffix + ".sha256")
    if sidecar.exists():
        if sidecar.is_symlink() or not sidecar.is_file():
            raise RuntimeError("release SHA256 sidecar is unsafe")
        fields = sidecar.read_text(encoding="ascii").strip().split()
        if len(fields) != 2 or fields[1] != path.name or fields[0] != _sha256_file(path):
            raise RuntimeError("release SHA256 sidecar does not match the archive")

    with tarfile.open(path, mode="r:gz") as archive:
        members = archive.getmembers()
        if not members or len(members) > MAX_ARCHIVE_MEMBERS:
            raise RuntimeError("release archive member count is invalid")
        names: set[str] = set()
        prefix: str | None = None
        by_relative: dict[str, tarfile.TarInfo] = {}
        total = 0
        for member in members:
            if not member.isfile() or member.issym() or member.islnk():
                raise RuntimeError(f"release contains a non-regular member: {member.name}")
            candidate = _safe_relative_path(member.name)
            if len(candidate.parts) < 2:
                raise RuntimeError(f"release member has no release prefix: {member.name}")
            if prefix is None:
                prefix = candidate.parts[0]
                if not re.fullmatch(r"nutrimaster-release-[0-9a-f]{20}", prefix):
                    raise RuntimeError("release archive prefix is invalid")
            if candidate.parts[0] != prefix or member.name in names:
                raise RuntimeError("release archive has mixed or duplicate member paths")
            names.add(member.name)
            relative = PurePosixPath(*candidate.parts[1:]).as_posix()
            _assert_safe_source_path(Path(relative))
            by_relative[relative] = member
            total += member.size
            if member.size > MAX_SOURCE_FILE_BYTES or total > MAX_SOURCE_TOTAL_BYTES:
                raise RuntimeError("release archive expands beyond its safety limit")

        if "RELEASE.json" not in by_relative or "SHA256SUMS" not in by_relative:
            raise RuntimeError("release metadata is incomplete")
        manifest = json.loads(_read_member(archive, by_relative["RELEASE.json"]))
        if not isinstance(manifest, dict) or manifest.get("format") != RELEASE_FORMAT:
            raise RuntimeError("release manifest format is invalid")
        if manifest.get("expected_model") != EXPECTED_MODEL:
            raise RuntimeError("release model contract is not deepseek-v4-flash")
        if prefix != f"nutrimaster-release-{manifest.get('release_id')}":
            raise RuntimeError("release prefix does not match RELEASE.json")
        entries = manifest.get("files")
        if not isinstance(entries, list):
            raise RuntimeError("release file table is invalid")
        expected_paths: set[str] = set()
        expected_sums: list[str] = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise RuntimeError("release file entry is invalid")
            relative = str(entry.get("path", ""))
            _safe_relative_path(relative)
            if relative in expected_paths or relative not in by_relative:
                raise RuntimeError(f"release file table path is invalid: {relative}")
            expected_paths.add(relative)
            data = _read_member(archive, by_relative[relative])
            _assert_no_secret_content(relative, data)
            digest = _sha256_bytes(data)
            if digest != entry.get("sha256") or len(data) != entry.get("size"):
                raise RuntimeError(f"release file hash/size mismatch: {relative}")
            expected_sums.append(f"{digest}  {relative}\n")
        if set(by_relative) != expected_paths | {"RELEASE.json", "SHA256SUMS"}:
            raise RuntimeError("release contains files outside RELEASE.json")
        sums = _read_member(archive, by_relative["SHA256SUMS"]).decode("ascii")
        if sums != "".join(expected_sums):
            raise RuntimeError("release SHA256SUMS is inconsistent")

        required = set(_EXACT_FILES) | {
            "src/nutrimaster/__init__.py",
            "src/nutrimaster/cli.py",
            "pi-runtime/src/server.js",
            "pi-runtime/src/runtime.js",
        }
        if not required.issubset(expected_paths):
            raise RuntimeError(
                f"release is missing required runtime files: {sorted(required - expected_paths)}"
            )
        canonical = "".join(
            f"{entry['path']}\0{int(str(entry['mode']), 8):o}\0{entry['size']}\0{entry['sha256']}\n"
            for entry in entries
        ).encode("utf-8")
        if _sha256_bytes(canonical) != manifest.get("source_digest"):
            raise RuntimeError("release source digest is inconsistent")

    return {
        "status": "ok",
        "release_id": manifest["release_id"],
        "expected_model": manifest["expected_model"],
        "file_count": len(entries),
        "expanded_bytes": sum(int(entry["size"]) for entry in entries),
        "git_head": manifest["git_head"],
        "dirty_selected_paths": manifest["dirty_selected_paths"],
        "untracked_selected_paths": manifest["untracked_selected_paths"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build or verify a minimal NutriMaster release")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "dist" / "releases",
    )
    verify = subparsers.add_parser("verify")
    verify.add_argument("archive", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    result = (
        build_release(args.output_dir)
        if args.command == "build"
        else verify_release(args.archive)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
