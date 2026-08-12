from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from .formal_project import LeanProjectLock
from .reproducibility import stable_json_hash


CORPUS_SCHEMA_VERSION = 1
PARSER_VERSION = "v0.8-source-parser-1"
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_']*|\d+")
_DECL_RE = re.compile(r"^\s*(theorem|lemma|def|abbrev)\s+([A-Za-z_][A-Za-z0-9_']*)\b(.*)$")
_NAMESPACE_RE = re.compile(r"^\s*namespace\s+([A-Za-z_][A-Za-z0-9_'.]*)\s*$")
_END_RE = re.compile(r"^\s*end(?:\s+([A-Za-z_][A-Za-z0-9_'.]*))?\s*$")
_IMPORT_RE = re.compile(r"^\s*import\s+(.+?)\s*$")
_MODULE_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*")


def tokenize(text: str) -> list[str]:
    output: list[str] = []
    for token in _TOKEN_RE.findall(text):
        output.append(token.lower())
        parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+", token)
        output.extend(part.lower() for part in parts if part)
    return output


def _module_for_path(path: str) -> str:
    value = Path(path)
    if value.suffix != ".lean":
        raise ValueError(f"formal corpus source must be a .lean file: {path}")
    return ".".join(value.with_suffix("").parts)


def _dependency_module(path: str) -> tuple[str, str]:
    parts = Path(path).parts
    if len(parts) < 2:
        raise ValueError(f"dependency source path must include package directory: {path}")
    package = parts[0]
    module = _module_for_path(Path(*parts[1:]).as_posix())
    return package, module


@dataclass(slots=True, frozen=True)
class CorpusPremise:
    id: str
    name: str
    module: str
    statement: str
    kind: str
    package: str
    source_kind: str
    source_path: str
    line: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class CorpusInfo:
    project_fingerprint: str
    fingerprint: str
    modules: int
    imports: int
    premises: int
    schema_version: int = CORPUS_SCHEMA_VERSION
    parser_version: str = PARSER_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _scan_source(text: str, *, module: str, package: str, source_kind: str, source_path: str) -> tuple[list[str], list[CorpusPremise]]:
    imports: list[str] = []
    premises: list[CorpusPremise] = []
    lines = text.splitlines()
    namespaces: list[str] = []
    index = 0
    while index < len(lines):
        raw = lines[index]
        import_match = _IMPORT_RE.match(raw)
        if import_match:
            for imported in _MODULE_RE.findall(import_match.group(1)):
                if imported not in imports:
                    imports.append(imported)
            index += 1
            continue
        namespace_match = _NAMESPACE_RE.match(raw)
        if namespace_match:
            namespaces.append(namespace_match.group(1))
            index += 1
            continue
        end_match = _END_RE.match(raw)
        if end_match:
            if namespaces:
                namespaces.pop()
            index += 1
            continue
        match = _DECL_RE.match(raw)
        if match is None:
            index += 1
            continue

        kind, short_name, tail = match.groups()
        declaration_lines = [tail.strip()]
        cursor = index
        while cursor + 1 < len(lines) and cursor - index < 24:
            joined = " ".join(declaration_lines)
            if ":=" in joined or re.search(r"\bwhere\b", joined):
                break
            cursor += 1
            continuation = lines[cursor].strip()
            if _DECL_RE.match(continuation) or _NAMESPACE_RE.match(continuation) or _END_RE.match(continuation) or _IMPORT_RE.match(continuation):
                cursor -= 1
                break
            declaration_lines.append(continuation)
        statement = " ".join(part for part in declaration_lines if part).strip()
        statement = statement.split(":=", 1)[0].strip()
        statement = re.sub(r"\s+where\s*$", "", statement).strip()
        qualified = ".".join([*namespaces, short_name]) if namespaces else short_name
        premise_id = "corpus-premise-" + stable_json_hash(
            {
                "module": module,
                "name": qualified,
                "statement": statement,
                "kind": kind,
                "package": package,
                "source_kind": source_kind,
                "source_path": source_path,
                "line": index + 1,
            }
        )[:32]
        premises.append(
            CorpusPremise(
                id=premise_id,
                name=qualified,
                module=module,
                statement=statement,
                kind=kind,
                package=package,
                source_kind=source_kind,
                source_path=source_path,
                line=index + 1,
            )
        )
        index = max(index + 1, cursor + 1)
    return imports, premises


class FormalCorpus:
    """SQLite-backed frozen formal corpus for project + dependency premise retrieval."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not self.path.is_file():
            raise ValueError(f"formal corpus does not exist: {self.path}")
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._info = self._read_info()

    @classmethod
    def build(cls, root: str | Path, lock: LeanProjectLock, output: str | Path) -> CorpusInfo:
        lock.verify_project(root)
        project_root = Path(root).resolve()
        destination = Path(output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            destination.unlink()
        conn = sqlite3.connect(destination)
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE modules (
                    module TEXT PRIMARY KEY,
                    package TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    source_path TEXT NOT NULL
                );
                CREATE TABLE imports (
                    module TEXT NOT NULL,
                    imported_module TEXT NOT NULL,
                    PRIMARY KEY (module, imported_module)
                );
                CREATE INDEX idx_imports_imported ON imports(imported_module);
                CREATE TABLE premises (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    module TEXT NOT NULL,
                    statement TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    package TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    line INTEGER NOT NULL
                );
                CREATE INDEX idx_premises_module ON premises(module);
                CREATE INDEX idx_premises_name ON premises(name);
                CREATE TABLE premise_tokens (
                    token TEXT NOT NULL,
                    premise_id TEXT NOT NULL,
                    PRIMARY KEY (token, premise_id)
                );
                CREATE INDEX idx_premise_tokens_id ON premise_tokens(premise_id);
                CREATE TABLE token_df (
                    token TEXT PRIMARY KEY,
                    doc_freq INTEGER NOT NULL
                );
                """
            )
            module_rows: list[tuple[str, str, str, str]] = []
            import_rows: list[tuple[str, str]] = []
            premise_rows: list[CorpusPremise] = []

            def ingest(path: Path, *, module: str, package: str, source_kind: str, source_path: str) -> None:
                module_rows.append((module, package, source_kind, source_path))
                imports, premises = _scan_source(
                    path.read_text(encoding="utf-8"),
                    module=module,
                    package=package,
                    source_kind=source_kind,
                    source_path=source_path,
                )
                import_rows.extend((module, imported) for imported in imports)
                premise_rows.extend(premises)

            for item in lock.files:
                if not item.path.endswith(".lean"):
                    continue
                ingest(
                    project_root / item.path,
                    module=_module_for_path(item.path),
                    package="root",
                    source_kind="project",
                    source_path=item.path,
                )

            if lock.dependencies:
                if lock.manifest is None:
                    raise ValueError("locked dependencies require lake-manifest.json")
                manifest_data = json.loads((project_root / lock.manifest.path).read_text(encoding="utf-8"))
                raw_packages_dir = manifest_data.get("packagesDir", ".lake/packages")
                if not isinstance(raw_packages_dir, str) or not raw_packages_dir.strip():
                    raise ValueError("lake-manifest.json packagesDir must be a non-empty relative path")
                packages_path = Path(raw_packages_dir.strip())
                if packages_path.is_absolute() or ".." in packages_path.parts or packages_path.as_posix() in {"", "."}:
                    raise ValueError("formal corpus requires packagesDir to stay inside the frozen project root")
                packages_dir = packages_path.as_posix()
                packages_root = project_root / packages_dir
                for item in lock.dependency_cache_files:
                    if not item.path.endswith(".lean"):
                        continue
                    package, module = _dependency_module(item.path)
                    ingest(
                        packages_root / item.path,
                        module=module,
                        package=package,
                        source_kind="dependency",
                        source_path=item.path,
                    )

            by_module: dict[str, tuple[str, str, str, str]] = {}
            for row in sorted(module_rows):
                existing = by_module.get(row[0])
                if existing is not None and existing != row:
                    raise ValueError(f"formal corpus contains duplicate module name from different sources: {row[0]}")
                by_module[row[0]] = row
            module_rows = [by_module[name] for name in sorted(by_module)]
            import_rows = sorted(set(import_rows))
            premise_rows.sort(key=lambda item: (item.module, item.name, item.line, item.id))

            conn.executemany("INSERT INTO modules VALUES (?, ?, ?, ?)", module_rows)
            conn.executemany("INSERT INTO imports VALUES (?, ?)", import_rows)
            conn.executemany(
                "INSERT INTO premises VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        item.id,
                        item.name,
                        item.module,
                        item.statement,
                        item.kind,
                        item.package,
                        item.source_kind,
                        item.source_path,
                        item.line,
                    )
                    for item in premise_rows
                ],
            )

            token_to_ids: dict[str, set[str]] = {}
            for premise in premise_rows:
                for token in set(tokenize(f"{premise.name} {premise.statement}")):
                    token_to_ids.setdefault(token, set()).add(premise.id)
            token_rows = sorted((token, premise_id) for token, ids in token_to_ids.items() for premise_id in ids)
            df_rows = sorted((token, len(ids)) for token, ids in token_to_ids.items())
            conn.executemany("INSERT INTO premise_tokens VALUES (?, ?)", token_rows)
            conn.executemany("INSERT INTO token_df VALUES (?, ?)", df_rows)

            canonical = {
                "schema_version": CORPUS_SCHEMA_VERSION,
                "parser_version": PARSER_VERSION,
                "project_fingerprint": lock.fingerprint,
                "modules": module_rows,
                "imports": import_rows,
                "premises": [item.to_dict() for item in premise_rows],
            }
            fingerprint = stable_json_hash(canonical)
            metadata = {
                "schema_version": str(CORPUS_SCHEMA_VERSION),
                "parser_version": PARSER_VERSION,
                "project_fingerprint": lock.fingerprint,
                "fingerprint": fingerprint,
                "modules": str(len(module_rows)),
                "imports": str(len(import_rows)),
                "premises": str(len(premise_rows)),
            }
            conn.executemany("INSERT INTO metadata VALUES (?, ?)", sorted(metadata.items()))
            conn.commit()
            return CorpusInfo(
                project_fingerprint=lock.fingerprint,
                fingerprint=fingerprint,
                modules=len(module_rows),
                imports=len(import_rows),
                premises=len(premise_rows),
            )
        except Exception:
            conn.close()
            try:
                destination.unlink()
            except FileNotFoundError:
                pass
            raise
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _read_info(self) -> CorpusInfo:
        rows = {row["key"]: row["value"] for row in self.conn.execute("SELECT key, value FROM metadata")}
        try:
            schema = int(rows["schema_version"])
            if schema != CORPUS_SCHEMA_VERSION:
                raise ValueError(f"unsupported formal corpus schema_version: {schema}")
            if rows["parser_version"] != PARSER_VERSION:
                raise ValueError("formal corpus parser version is not supported by this ResearchEvolve build")
            info = CorpusInfo(
                project_fingerprint=rows["project_fingerprint"],
                fingerprint=rows["fingerprint"],
                modules=int(rows["modules"]),
                imports=int(rows["imports"]),
                premises=int(rows["premises"]),
                schema_version=schema,
                parser_version=rows["parser_version"],
            )
        except KeyError as exc:
            raise ValueError(f"formal corpus metadata is incomplete: {exc}") from exc
        if not re.fullmatch(r"[0-9a-f]{64}", info.project_fingerprint) or not re.fullmatch(r"[0-9a-f]{64}", info.fingerprint):
            raise ValueError("formal corpus fingerprints are invalid")
        return info

    @property
    def info(self) -> CorpusInfo:
        return self._info

    @property
    def fingerprint(self) -> str:
        return self._info.fingerprint

    @property
    def project_fingerprint(self) -> str:
        return self._info.project_fingerprint

    def reachable_modules(self, roots: Iterable[str]) -> dict[str, int]:
        queue = [(str(root), 0) for root in roots if str(root)]
        distance: dict[str, int] = {}
        cursor = 0
        while cursor < len(queue):
            module, depth = queue[cursor]
            cursor += 1
            previous = distance.get(module)
            if previous is not None and previous <= depth:
                continue
            distance[module] = depth
            for row in self.conn.execute(
                "SELECT imported_module FROM imports WHERE module = ? ORDER BY imported_module",
                (module,),
            ):
                queue.append((str(row["imported_module"]), depth + 1))
        return distance

    def candidate_premises(self, tokens: Iterable[str], reachable: dict[str, int], *, candidate_limit: int = 512) -> list[CorpusPremise]:
        unique = sorted(set(tokens))
        if not unique or not reachable:
            return []
        placeholders = ",".join("?" for _ in unique)
        premise_ids = [
            row["premise_id"]
            for row in self.conn.execute(
                f"""
                SELECT premise_id, COUNT(*) AS matches
                FROM premise_tokens
                WHERE token IN ({placeholders})
                GROUP BY premise_id
                ORDER BY matches DESC, premise_id
                LIMIT ?
                """,
                (*unique, int(candidate_limit)),
            )
        ]
        if not premise_ids:
            return []
        id_placeholders = ",".join("?" for _ in premise_ids)
        rows = self.conn.execute(
            f"SELECT * FROM premises WHERE id IN ({id_placeholders})",
            premise_ids,
        ).fetchall()
        output: list[CorpusPremise] = []
        for row in rows:
            if str(row["module"]) not in reachable:
                continue
            output.append(
                CorpusPremise(
                    id=str(row["id"]),
                    name=str(row["name"]),
                    module=str(row["module"]),
                    statement=str(row["statement"]),
                    kind=str(row["kind"]),
                    package=str(row["package"]),
                    source_kind=str(row["source_kind"]),
                    source_path=str(row["source_path"]),
                    line=int(row["line"]),
                )
            )
        return output

    def token_document_frequency(self, token: str) -> int:
        row = self.conn.execute("SELECT doc_freq FROM token_df WHERE token = ?", (token,)).fetchone()
        return int(row["doc_freq"]) if row is not None else 0

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "FormalCorpus":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()
