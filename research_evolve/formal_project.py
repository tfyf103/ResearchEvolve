from __future__ import annotations

import json
import re
import shlex
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Sequence

from .reproducibility import sha256_file, stable_json_hash


_LOCK_SCHEMA_VERSION = 2
_EXCLUDED_DIRS = {".git", ".lake", "__pycache__"}


def _normalize_rel(path: str | Path) -> str:
    value = Path(path).as_posix().strip()
    if value in {"", "."}:
        return "."
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"project path must stay relative to the project root: {value!r}")
    return candidate.as_posix()


def _assert_regular_file(root: Path, relative: str) -> Path:
    path = root / relative
    if path.is_symlink():
        raise ValueError(f"project lock refuses symlinked file: {relative}")
    if not path.is_file():
        raise ValueError(f"project lock file does not exist: {relative}")
    return path


def _dependency_declaration_present(lakefile: Path) -> bool:
    text = lakefile.read_text(encoding="utf-8")
    if lakefile.suffix == ".toml":
        return "[[require]]" in text
    return re.search(r"(?m)^\s*require\b", text) is not None


def _manifest_dependencies(manifest: Any) -> list[dict[str, Any]]:
    if not isinstance(manifest, dict):
        raise ValueError("lake-manifest.json must contain a JSON object")
    packages = manifest.get("packages", [])
    if not isinstance(packages, list):
        raise ValueError("lake-manifest.json packages must be a list")
    normalized: list[dict[str, Any]] = []
    for index, package in enumerate(packages):
        if not isinstance(package, dict):
            raise ValueError(f"lake-manifest package #{index} must be an object")
        normalized.append(json.loads(json.dumps(package, sort_keys=True)))
    normalized.sort(key=lambda item: (str(item.get("name", "")), str(item.get("scope", ""))))
    return normalized


def _manifest_packages_dir(manifest: Any) -> str:
    if not isinstance(manifest, dict):
        raise ValueError("lake-manifest.json must contain a JSON object")
    raw = manifest.get("packagesDir", ".lake/packages")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("lake-manifest.json packagesDir must be a non-empty relative path")
    try:
        relative = _normalize_rel(raw.strip())
    except ValueError as exc:
        raise ValueError(
            "v0.7 certification requires lake-manifest.json packagesDir to stay inside the frozen project root"
        ) from exc
    if relative == ".":
        raise ValueError("lake-manifest.json packagesDir must not be the project root")
    return relative


def _is_within(relative: str, directory: str) -> bool:
    path_parts = Path(relative).parts
    directory_parts = Path(directory).parts
    return len(path_parts) >= len(directory_parts) and path_parts[: len(directory_parts)] == directory_parts


@dataclass(slots=True, frozen=True)
class LockedProjectFile:
    path: str
    sha256: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "sha256": self.sha256}


def _capture_dependency_cache(
    project_root: Path,
    dependencies: list[dict[str, Any]],
    packages_dir: str,
) -> list[LockedProjectFile]:
    if not dependencies:
        return []
    packages = project_root / packages_dir
    if not packages.is_dir():
        raise ValueError(
            f"Lake manifest contains dependencies but {packages_dir} is missing; "
            "run lake update/fetch before creating a reproducible project lock"
        )
    tracked: dict[str, LockedProjectFile] = {}
    for path in sorted(packages.rglob("*")):
        relative_path = path.relative_to(packages)
        if any(part in _EXCLUDED_DIRS for part in relative_path.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"project lock refuses symlinked dependency-cache path: {relative_path.as_posix()}")
        if not path.is_file():
            continue
        relative = relative_path.as_posix()
        tracked[relative] = LockedProjectFile(relative, sha256_file(path))
    files = [tracked[key] for key in sorted(tracked)]
    if not files:
        raise ValueError(
            f"Lake manifest contains dependencies but no lockable files were found under {packages_dir}; "
            "v0.7 only supports cached package dependencies"
        )
    return files


@dataclass(slots=True)
class LeanProjectLock:
    """Content-addressed lock for a Lean/Lake project used by formal verification."""

    toolchain: str
    lakefile: LockedProjectFile
    files: list[LockedProjectFile]
    source_roots: list[str] = field(default_factory=lambda: ["."])
    extra_paths: list[str] = field(default_factory=list)
    manifest: LockedProjectFile | None = None
    dependencies: list[dict[str, Any]] = field(default_factory=list)
    dependency_cache_files: list[LockedProjectFile] = field(default_factory=list)
    fingerprint: str = ""
    schema_version: int = _LOCK_SCHEMA_VERSION

    @staticmethod
    def _stable_payload(
        *,
        toolchain: str,
        lakefile: LockedProjectFile,
        files: list[LockedProjectFile],
        source_roots: list[str],
        extra_paths: list[str],
        manifest: LockedProjectFile | None,
        dependencies: list[dict[str, Any]],
        dependency_cache_files: list[LockedProjectFile],
    ) -> dict[str, Any]:
        return {
            "toolchain": toolchain.strip(),
            "lakefile": lakefile.to_dict(),
            "manifest": manifest.to_dict() if manifest is not None else None,
            "source_roots": list(source_roots),
            "extra_paths": list(extra_paths),
            "files": [item.to_dict() for item in files],
            "dependencies": dependencies,
            "dependency_cache_files": [item.to_dict() for item in dependency_cache_files],
        }

    @classmethod
    def capture(
        cls,
        root: str | Path,
        *,
        source_roots: Sequence[str | Path] | None = None,
        extra_paths: Sequence[str | Path] | None = None,
        allow_unlocked_dependencies: bool = False,
    ) -> "LeanProjectLock":
        project_root = Path(root).resolve()
        if not project_root.is_dir():
            raise ValueError(f"Lean project root does not exist: {project_root}")

        toolchain_path = _assert_regular_file(project_root, "lean-toolchain")
        toolchain = toolchain_path.read_text(encoding="utf-8").strip()
        if not toolchain:
            raise ValueError("Lean project lean-toolchain must not be empty")

        lakefile_path: Path | None = None
        for name in ("lakefile.toml", "lakefile.lean"):
            candidate = project_root / name
            if candidate.is_file():
                if candidate.is_symlink():
                    raise ValueError(f"project lock refuses symlinked file: {name}")
                lakefile_path = candidate
                break
        if lakefile_path is None:
            raise ValueError("Lean project requires lakefile.toml or lakefile.lean")
        lakefile = LockedProjectFile(lakefile_path.name, sha256_file(lakefile_path))

        manifest_path = project_root / "lake-manifest.json"
        manifest: LockedProjectFile | None = None
        dependencies: list[dict[str, Any]] = []
        packages_dir = ".lake/packages"
        if manifest_path.is_file():
            if manifest_path.is_symlink():
                raise ValueError("project lock refuses symlinked lake-manifest.json")
            manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest = LockedProjectFile("lake-manifest.json", sha256_file(manifest_path))
            dependencies = _manifest_dependencies(manifest_data)
            packages_dir = _manifest_packages_dir(manifest_data)
        elif _dependency_declaration_present(lakefile_path) and not allow_unlocked_dependencies:
            raise ValueError(
                "Lake project declares dependencies but has no lake-manifest.json; run lake update first or explicitly allow unlocked dependencies"
            )

        dependency_cache_files = _capture_dependency_cache(project_root, dependencies, packages_dir)
        roots = [_normalize_rel(item) for item in (source_roots or ["."])]
        extras = [_normalize_rel(item) for item in (extra_paths or [])]
        tracked: dict[str, LockedProjectFile] = {}

        def add_file(path: Path) -> None:
            if path.is_symlink():
                raise ValueError(f"project lock refuses symlinked source: {path}")
            relative = path.relative_to(project_root).as_posix()
            if any(part in _EXCLUDED_DIRS for part in Path(relative).parts):
                return
            if dependencies and _is_within(relative, packages_dir):
                return
            tracked[relative] = LockedProjectFile(relative, sha256_file(path))

        for root_name in roots:
            source_root = project_root if root_name == "." else project_root / root_name
            if source_root.is_file():
                if source_root.suffix == ".lean":
                    add_file(source_root)
                continue
            if not source_root.is_dir():
                raise ValueError(f"source root does not exist: {root_name}")
            for path in sorted(source_root.rglob("*.lean")):
                add_file(path)

        for relative in extras:
            path = _assert_regular_file(project_root, relative)
            add_file(path)

        files = [tracked[key] for key in sorted(tracked)]
        if not files:
            raise ValueError("Lean project lock must track at least one .lean or extra file")

        stable = cls._stable_payload(
            toolchain=toolchain,
            lakefile=lakefile,
            files=files,
            source_roots=roots,
            extra_paths=extras,
            manifest=manifest,
            dependencies=dependencies,
            dependency_cache_files=dependency_cache_files,
        )
        return cls(
            toolchain=toolchain,
            lakefile=lakefile,
            files=files,
            source_roots=roots,
            extra_paths=extras,
            manifest=manifest,
            dependencies=dependencies,
            dependency_cache_files=dependency_cache_files,
            fingerprint=stable_json_hash(stable),
        )

    @staticmethod
    def _locked_files(raw: Any, label: str) -> list[LockedProjectFile]:
        if not isinstance(raw, list):
            raise ValueError(f"Lean project lock {label} must be a list")
        files: list[LockedProjectFile] = []
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                raise ValueError(f"Lean project lock {label} item #{index} must be an object")
            files.append(LockedProjectFile(str(item.get("path", "")), str(item.get("sha256", ""))))
        return files

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "LeanProjectLock":
        if int(raw.get("schema_version", -1)) != _LOCK_SCHEMA_VERSION:
            raise ValueError("unsupported Lean project lock schema_version")
        lakefile_raw = raw.get("lakefile")
        if not isinstance(lakefile_raw, dict):
            raise ValueError("Lean project lock requires lakefile")
        manifest_raw = raw.get("manifest")
        files = cls._locked_files(raw.get("files", []), "files")
        dependency_cache_files = cls._locked_files(raw.get("dependency_cache_files", []), "dependency_cache_files")
        dependencies = raw.get("dependencies", [])
        if not isinstance(dependencies, list) or not all(isinstance(item, dict) for item in dependencies):
            raise ValueError("Lean project lock dependencies must be a list of objects")
        lock = cls(
            toolchain=str(raw.get("toolchain", "")),
            lakefile=LockedProjectFile(str(lakefile_raw.get("path", "")), str(lakefile_raw.get("sha256", ""))),
            files=files,
            source_roots=[_normalize_rel(item) for item in raw.get("source_roots", ["."])],
            extra_paths=[_normalize_rel(item) for item in raw.get("extra_paths", [])],
            manifest=(
                LockedProjectFile(str(manifest_raw.get("path", "")), str(manifest_raw.get("sha256", "")))
                if isinstance(manifest_raw, dict)
                else None
            ),
            dependencies=[dict(item) for item in dependencies],
            dependency_cache_files=dependency_cache_files,
            fingerprint=str(raw.get("fingerprint", "")),
            schema_version=int(raw.get("schema_version", _LOCK_SCHEMA_VERSION)),
        )
        lock.validate()
        return lock

    @classmethod
    def read(cls, path: str | Path) -> "LeanProjectLock":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Lean project lock must contain a JSON object")
        return cls.from_dict(raw)

    @staticmethod
    def _validate_file_list(files: list[LockedProjectFile], label: str) -> None:
        paths = [item.path for item in files]
        if len(paths) != len(set(paths)):
            raise ValueError(f"Lean project lock contains duplicate {label} paths")
        if paths != sorted(paths):
            raise ValueError(f"Lean project lock {label} must be sorted")
        for item in files:
            _normalize_rel(item.path)
            if not re.fullmatch(r"[0-9a-f]{64}", item.sha256):
                raise ValueError(f"invalid sha256 for locked {label} file {item.path!r}")

    def validate(self) -> None:
        if not self.toolchain.strip():
            raise ValueError("Lean project lock toolchain must not be empty")
        if not self.lakefile.path or not re.fullmatch(r"[0-9a-f]{64}", self.lakefile.sha256):
            raise ValueError("Lean project lock lakefile is incomplete or has invalid sha256")
        if self.lakefile.path not in {"lakefile.toml", "lakefile.lean"}:
            raise ValueError("Lean project lock lakefile must be lakefile.toml or lakefile.lean")
        self._validate_file_list(self.files, "tracked files")
        self._validate_file_list(self.dependency_cache_files, "dependency cache files")
        if self.manifest is not None:
            if self.manifest.path != "lake-manifest.json" or not re.fullmatch(r"[0-9a-f]{64}", self.manifest.sha256):
                raise ValueError("invalid locked lake-manifest.json")
        if self.dependencies and not self.dependency_cache_files:
            raise ValueError("locked dependencies require content-addressed dependency_cache_files")
        if self.dependency_cache_files and not self.dependencies:
            raise ValueError("dependency_cache_files are not allowed without locked manifest dependencies")
        stable = self._stable_payload(
            toolchain=self.toolchain,
            lakefile=self.lakefile,
            files=self.files,
            source_roots=self.source_roots,
            extra_paths=self.extra_paths,
            manifest=self.manifest,
            dependencies=self.dependencies,
            dependency_cache_files=self.dependency_cache_files,
        )
        expected = stable_json_hash(stable)
        if self.fingerprint != expected:
            raise ValueError("Lean project lock fingerprint does not match its frozen contents")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            **self._stable_payload(
                toolchain=self.toolchain,
                lakefile=self.lakefile,
                files=self.files,
                source_roots=self.source_roots,
                extra_paths=self.extra_paths,
                manifest=self.manifest,
                dependencies=self.dependencies,
                dependency_cache_files=self.dependency_cache_files,
            ),
            "fingerprint": self.fingerprint,
        }

    def write(self, path: str | Path) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    def verify_project(self, root: str | Path) -> None:
        # Certification is intentionally stricter than lock capture. A developer may
        # capture an unlocked dependency declaration for inspection, but any project
        # used by premise indexing or ProjectLeanKernel must resolve dependencies into
        # lake-manifest.json + content-addressed dependency sources.
        current = LeanProjectLock.capture(
            root,
            source_roots=self.source_roots,
            extra_paths=self.extra_paths,
            allow_unlocked_dependencies=False,
        )
        if current.fingerprint != self.fingerprint:
            raise ValueError(
                "Lean project no longer matches frozen project lock: "
                f"expected {self.fingerprint}, got {current.fingerprint}"
            )


@dataclass(slots=True)
class LeanProjectEnvironment:
    root: Path
    lock: LeanProjectLock
    lake_command: list[str] = field(default_factory=lambda: ["lake"])
    build_targets: list[str] = field(default_factory=list)
    copy_dependency_cache: bool = True

    @classmethod
    def create(
        cls,
        root: str | Path,
        lock: LeanProjectLock,
        *,
        lake_command: str | Sequence[str] = "lake",
        build_targets: Sequence[str] | None = None,
        copy_dependency_cache: bool = True,
    ) -> "LeanProjectEnvironment":
        command = shlex.split(lake_command) if isinstance(lake_command, str) else [str(item) for item in lake_command]
        if not command:
            raise ValueError("lake command must not be empty")
        environment = cls(
            root=Path(root).resolve(),
            lock=lock,
            lake_command=command,
            build_targets=[str(item) for item in (build_targets or [])],
            copy_dependency_cache=bool(copy_dependency_cache),
        )
        environment.validate()
        return environment

    @property
    def name(self) -> str:
        command_hash = stable_json_hash(self.lake_command)[:12]
        return f"lean-project:{self.lock.fingerprint[:16]}:lake:{command_hash}"

    @property
    def fingerprint(self) -> str:
        return self.lock.fingerprint

    def validate(self) -> None:
        self.lock.validate()
        if not self.root.is_dir():
            raise ValueError(f"Lean project root does not exist: {self.root}")
        self.lock.verify_project(self.root)

    def _packages_dir(self) -> str:
        if self.lock.manifest is None:
            return ".lake/packages"
        manifest_path = _assert_regular_file(self.root, self.lock.manifest.path)
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        return _manifest_packages_dir(manifest_data)

    @contextmanager
    def materialize(self, workspace: str | Path) -> Iterator[Path]:
        self.validate()
        workspace_root = Path(workspace)
        workspace_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="researchevolve-project-", dir=workspace_root) as temporary:
            destination = Path(temporary)

            def copy_relative(relative: str) -> None:
                source = _assert_regular_file(self.root, relative)
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)

            copy_relative("lean-toolchain")
            copy_relative(self.lock.lakefile.path)
            if self.lock.manifest is not None:
                copy_relative(self.lock.manifest.path)
            for item in self.lock.files:
                copy_relative(item.path)

            if self.lock.dependencies:
                if not self.copy_dependency_cache:
                    raise ValueError(
                        "frozen project has dependencies but copy_dependency_cache=false; v0.7 refuses a network-dependent sandbox"
                    )
                packages_dir = self._packages_dir()
                packages = self.root / packages_dir
                for item in self.lock.dependency_cache_files:
                    source = _assert_regular_file(packages, item.path)
                    target = destination / packages_dir / item.path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)

            yield destination
