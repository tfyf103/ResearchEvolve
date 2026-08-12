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


_LOCK_SCHEMA_VERSION = 1
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


@dataclass(slots=True, frozen=True)
class LockedProjectFile:
    path: str
    sha256: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "sha256": self.sha256}


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
    ) -> dict[str, Any]:
        return {
            "toolchain": toolchain.strip(),
            "lakefile": lakefile.to_dict(),
            "manifest": manifest.to_dict() if manifest is not None else None,
            "source_roots": list(source_roots),
            "extra_paths": list(extra_paths),
            "files": [item.to_dict() for item in files],
            "dependencies": dependencies,
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
        if manifest_path.is_file():
            if manifest_path.is_symlink():
                raise ValueError("project lock refuses symlinked lake-manifest.json")
            manifest = LockedProjectFile("lake-manifest.json", sha256_file(manifest_path))
            dependencies = _manifest_dependencies(json.loads(manifest_path.read_text(encoding="utf-8")))
        elif _dependency_declaration_present(lakefile_path) and not allow_unlocked_dependencies:
            raise ValueError(
                "Lake project declares dependencies but has no lake-manifest.json; run lake update first or explicitly allow unlocked dependencies"
            )

        roots = [_normalize_rel(item) for item in (source_roots or ["."])]
        extras = [_normalize_rel(item) for item in (extra_paths or [])]
        tracked: dict[str, LockedProjectFile] = {}

        def add_file(path: Path) -> None:
            if path.is_symlink():
                raise ValueError(f"project lock refuses symlinked source: {path}")
            relative = path.relative_to(project_root).as_posix()
            if any(part in _EXCLUDED_DIRS for part in Path(relative).parts):
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
        )
        return cls(
            toolchain=toolchain,
            lakefile=lakefile,
            files=files,
            source_roots=roots,
            extra_paths=extras,
            manifest=manifest,
            dependencies=dependencies,
            fingerprint=stable_json_hash(stable),
        )

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "LeanProjectLock":
        if int(raw.get("schema_version", -1)) != _LOCK_SCHEMA_VERSION:
            raise ValueError("unsupported Lean project lock schema_version")
        lakefile_raw = raw.get("lakefile")
        if not isinstance(lakefile_raw, dict):
            raise ValueError("Lean project lock requires lakefile")
        manifest_raw = raw.get("manifest")
        files_raw = raw.get("files", [])
        if not isinstance(files_raw, list):
            raise ValueError("Lean project lock files must be a list")
        dependencies = raw.get("dependencies", [])
        if not isinstance(dependencies, list) or not all(isinstance(item, dict) for item in dependencies):
            raise ValueError("Lean project lock dependencies must be a list of objects")
        lock = cls(
            toolchain=str(raw.get("toolchain", "")),
            lakefile=LockedProjectFile(str(lakefile_raw.get("path", "")), str(lakefile_raw.get("sha256", ""))),
            files=[LockedProjectFile(str(item.get("path", "")), str(item.get("sha256", ""))) for item in files_raw if isinstance(item, dict)],
            source_roots=[_normalize_rel(item) for item in raw.get("source_roots", ["."])],
            extra_paths=[_normalize_rel(item) for item in raw.get("extra_paths", [])],
            manifest=(
                LockedProjectFile(str(manifest_raw.get("path", "")), str(manifest_raw.get("sha256", "")))
                if isinstance(manifest_raw, dict)
                else None
            ),
            dependencies=[dict(item) for item in dependencies],
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

    def validate(self) -> None:
        if not self.toolchain.strip():
            raise ValueError("Lean project lock toolchain must not be empty")
        if not self.lakefile.path or not self.lakefile.sha256:
            raise ValueError("Lean project lock lakefile is incomplete")
        if self.lakefile.path not in {"lakefile.toml", "lakefile.lean"}:
            raise ValueError("Lean project lock lakefile must be lakefile.toml or lakefile.lean")
        paths = [item.path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("Lean project lock contains duplicate tracked files")
        if paths != sorted(paths):
            raise ValueError("Lean project lock tracked files must be sorted")
        for item in self.files:
            _normalize_rel(item.path)
            if not re.fullmatch(r"[0-9a-f]{64}", item.sha256):
                raise ValueError(f"invalid sha256 for locked project file {item.path!r}")
        if self.manifest is not None:
            if self.manifest.path != "lake-manifest.json" or not re.fullmatch(r"[0-9a-f]{64}", self.manifest.sha256):
                raise ValueError("invalid locked lake-manifest.json")
        stable = self._stable_payload(
            toolchain=self.toolchain,
            lakefile=self.lakefile,
            files=self.files,
            source_roots=self.source_roots,
            extra_paths=self.extra_paths,
            manifest=self.manifest,
            dependencies=self.dependencies,
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
            ),
            "fingerprint": self.fingerprint,
        }

    def write(self, path: str | Path) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    def verify_project(self, root: str | Path) -> None:
        current = LeanProjectLock.capture(
            root,
            source_roots=self.source_roots,
            extra_paths=self.extra_paths,
            allow_unlocked_dependencies=self.manifest is None and not self.dependencies,
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
                packages = self.root / ".lake" / "packages"
                if not self.copy_dependency_cache:
                    raise ValueError(
                        "frozen project has dependencies but copy_dependency_cache=false; v0.7 refuses a network-dependent sandbox"
                    )
                if not packages.is_dir():
                    raise ValueError(
                        "frozen project has dependencies but .lake/packages is missing; resolve/cache dependencies before formal verification"
                    )
                shutil.copytree(packages, destination / ".lake" / "packages", symlinks=False)

            yield destination
