from __future__ import annotations

import json
import math
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .formal_project import LeanProjectLock
from .reproducibility import stable_json_hash


_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_']*|\d+")
_DECL_RE = re.compile(r"^\s*(theorem|lemma|def|abbrev)\s+([A-Za-z_][A-Za-z0-9_']*)\b(.*)$")
_NAMESPACE_RE = re.compile(r"^\s*namespace\s+([A-Za-z_][A-Za-z0-9_'.]*)\s*$")
_END_RE = re.compile(r"^\s*end(?:\s+([A-Za-z_][A-Za-z0-9_'.]*))?\s*$")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tokens(text: str) -> list[str]:
    output: list[str] = []
    for token in _TOKEN_RE.findall(text):
        lowered = token.lower()
        output.append(lowered)
        parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+", token)
        output.extend(part.lower() for part in parts if part)
    return output


def _module_for_path(path: str) -> str:
    value = Path(path)
    if value.suffix != ".lean":
        raise ValueError(f"premise source must be a .lean file: {path}")
    return ".".join(value.with_suffix("").parts)


@dataclass(slots=True, frozen=True)
class Premise:
    name: str
    module: str
    statement: str
    kind: str
    source_path: str
    line: int
    tags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "module": self.module,
            "statement": self.statement,
            "kind": self.kind,
            "source_path": self.source_path,
            "line": self.line,
            "tags": list(self.tags),
        }


@dataclass(slots=True)
class PremiseIndex:
    project_fingerprint: str
    premises: list[Premise]
    fingerprint: str = ""
    schema_version: int = 1

    @classmethod
    def build_from_project(cls, root: str | Path, lock: LeanProjectLock) -> "PremiseIndex":
        lock.verify_project(root)
        project_root = Path(root).resolve()
        premises: list[Premise] = []
        locked_lean = [item for item in lock.files if item.path.endswith(".lean")]
        for locked in locked_lean:
            path = project_root / locked.path
            module = _module_for_path(locked.path)
            lines = path.read_text(encoding="utf-8").splitlines()
            namespaces: list[str] = []
            index = 0
            while index < len(lines):
                raw = lines[index]
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
                while cursor + 1 < len(lines) and cursor - index < 12:
                    joined = " ".join(declaration_lines)
                    if ":=" in joined or re.search(r"\bwhere\b", joined):
                        break
                    cursor += 1
                    continuation = lines[cursor].strip()
                    if _DECL_RE.match(continuation) or _NAMESPACE_RE.match(continuation) or _END_RE.match(continuation):
                        cursor -= 1
                        break
                    declaration_lines.append(continuation)
                statement = " ".join(part for part in declaration_lines if part).strip()
                statement = statement.split(":=", 1)[0].strip()
                statement = re.sub(r"\s+where\s*$", "", statement).strip()
                qualified = ".".join([*namespaces, short_name]) if namespaces else short_name
                premises.append(
                    Premise(
                        name=qualified,
                        module=module,
                        statement=statement,
                        kind=kind,
                        source_path=locked.path,
                        line=index + 1,
                    )
                )
                index = max(index + 1, cursor + 1)

        premises.sort(key=lambda item: (item.module, item.name, item.line))
        stable = {
            "project_fingerprint": lock.fingerprint,
            "premises": [item.to_dict() for item in premises],
        }
        return cls(
            project_fingerprint=lock.fingerprint,
            premises=premises,
            fingerprint=stable_json_hash(stable),
        )

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PremiseIndex":
        if int(raw.get("schema_version", -1)) != 1:
            raise ValueError("unsupported premise index schema_version")
        raw_premises = raw.get("premises", [])
        if not isinstance(raw_premises, list):
            raise ValueError("premise index premises must be a list")
        premises: list[Premise] = []
        for index, item in enumerate(raw_premises):
            if not isinstance(item, dict):
                raise ValueError(f"premise #{index} must be an object")
            tags = item.get("tags", [])
            if not isinstance(tags, list):
                raise ValueError(f"premise #{index} tags must be a list")
            premises.append(
                Premise(
                    name=str(item.get("name", "")),
                    module=str(item.get("module", "")),
                    statement=str(item.get("statement", "")),
                    kind=str(item.get("kind", "")),
                    source_path=str(item.get("source_path", "")),
                    line=int(item.get("line", 0)),
                    tags=tuple(str(tag) for tag in tags),
                )
            )
        instance = cls(
            project_fingerprint=str(raw.get("project_fingerprint", "")),
            premises=premises,
            fingerprint=str(raw.get("fingerprint", "")),
            schema_version=int(raw.get("schema_version", 1)),
        )
        instance.validate()
        return instance

    @classmethod
    def read(cls, path: str | Path) -> "PremiseIndex":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("premise index must contain a JSON object")
        return cls.from_dict(raw)

    def validate(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", self.project_fingerprint):
            raise ValueError("premise index project_fingerprint must be a SHA-256-like fingerprint")
        names = [(item.module, item.name, item.line) for item in self.premises]
        if names != sorted(names):
            raise ValueError("premise index entries must be sorted")
        for premise in self.premises:
            if not premise.name or not premise.module or premise.line < 1:
                raise ValueError("premise index contains an incomplete declaration")
        stable = {
            "project_fingerprint": self.project_fingerprint,
            "premises": [item.to_dict() for item in self.premises],
        }
        if stable_json_hash(stable) != self.fingerprint:
            raise ValueError("premise index fingerprint does not match its contents")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "project_fingerprint": self.project_fingerprint,
            "premises": [item.to_dict() for item in self.premises],
            "fingerprint": self.fingerprint,
        }

    def write(self, path: str | Path) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")


@dataclass(slots=True, frozen=True)
class ScoredPremise:
    premise: Premise
    score: float
    matched_tokens: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.premise.to_dict(),
            "score": self.score,
            "matched_tokens": list(self.matched_tokens),
        }


@dataclass(slots=True)
class PremiseSelection:
    formal_spec_id: str
    query: str
    index_fingerprint: str
    selected: list[ScoredPremise]
    id: str = field(default_factory=lambda: f"premise-selection-{uuid.uuid4().hex}")
    created_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "formal_spec_id": self.formal_spec_id,
            "query": self.query,
            "index_fingerprint": self.index_fingerprint,
            "selected": [item.to_dict() for item in self.selected],
            "created_at": self.created_at,
        }


class PremiseSelector:
    """Deterministic lexical premise retrieval bound to a frozen project index."""

    def __init__(self, index: PremiseIndex, *, limit: int = 12) -> None:
        if limit < 1:
            raise ValueError("premise selection limit must be positive")
        index.validate()
        self.index = index
        self.limit = int(limit)
        document_frequency: dict[str, int] = {}
        for premise in index.premises:
            for token in set(_tokens(f"{premise.name} {premise.statement} {' '.join(premise.tags)}")):
                document_frequency[token] = document_frequency.get(token, 0) + 1
        self._document_frequency = document_frequency

    @property
    def name(self) -> str:
        return f"premise-selector:{self.index.fingerprint[:16]}:limit={self.limit}"

    def select(
        self,
        *,
        formal_spec_id: str,
        query: str,
        allowed_modules: Sequence[str] | None = None,
    ) -> PremiseSelection:
        query_tokens = set(_tokens(query))
        # None means an intentionally unrestricted preview/search. An explicit empty
        # sequence means an empty allowlist. This distinction matters for the trusted
        # formalization path: FormalizationSpec.imports=[] must not silently expose
        # every indexed module to the Formalizer/Repairer.
        allowed = None if allowed_modules is None else set(allowed_modules)
        scores: list[ScoredPremise] = []
        total_docs = max(1, len(self.index.premises))
        for premise in self.index.premises:
            if allowed is not None and premise.module not in allowed:
                continue
            premise_tokens = set(_tokens(f"{premise.name} {premise.statement} {' '.join(premise.tags)}"))
            matched = sorted(query_tokens & premise_tokens)
            if not matched:
                continue
            lexical = sum(
                math.log((total_docs + 1) / (self._document_frequency.get(token, 0) + 1)) + 1.0
                for token in matched
            )
            name_tokens = set(_tokens(premise.name))
            name_bonus = 0.75 * len(query_tokens & name_tokens)
            score = lexical + name_bonus
            scores.append(ScoredPremise(premise=premise, score=round(score, 6), matched_tokens=tuple(matched)))
        scores.sort(key=lambda item: (-item.score, item.premise.name, item.premise.module))
        return PremiseSelection(
            formal_spec_id=formal_spec_id,
            query=query,
            index_fingerprint=self.index.fingerprint,
            selected=scores[: self.limit],
        )


class FormalRetrievalMemory:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS premise_selections (
                id TEXT PRIMARY KEY,
                formal_spec_id TEXT NOT NULL,
                index_fingerprint TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_premise_selection_spec ON premise_selections(formal_spec_id);
            """
        )
        self.conn.commit()

    def record(self, selection: PremiseSelection) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO premise_selections VALUES (?, ?, ?, ?, ?)",
            (
                selection.id,
                selection.formal_spec_id,
                selection.index_fingerprint,
                json.dumps(selection.to_dict(), sort_keys=True),
                selection.created_at,
            ),
        )
        self.conn.commit()

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT payload FROM premise_selections ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def close(self) -> None:
        self.conn.close()
