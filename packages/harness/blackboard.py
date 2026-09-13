"""Versioned shared Artifact Blackboard with memory and PostgreSQL backends."""

import asyncio
import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any, Protocol, Self

from pydantic import AwareDatetime, Field, JsonValue, model_validator
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncEngine

from packages.domain import ArtifactRef
from packages.domain.common import (
    DomainModel,
    Identifier,
    SchemaVersion,
    Sha256Digest,
    ShortText,
    ensure_unique,
)
from packages.harness.persistence.schema import artifact_heads, artifact_parents, artifacts


def artifact_content_hash(payload: Mapping[str, JsonValue]) -> str:
    """Hash canonical JSON so storage and consumers can verify immutable content."""

    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


class ArtifactVersionConflictError(RuntimeError):
    """Optimistic-write failure containing no payload or secret values."""


class ArtifactNotFoundError(LookupError):
    """Raised when a requested Artifact or lineage parent does not exist."""


class ArtifactRecord(DomainModel):
    """One immutable, content-addressed Blackboard version."""

    artifact_id: Identifier
    run_id: Identifier
    task_id: Identifier
    artifact_type: ShortText
    schema_version: SchemaVersion = "1.0"
    version: Annotated[int, Field(ge=1)]
    producer_agent: Identifier
    parent_artifact_ids: tuple[Identifier, ...] = ()
    content_hash: Sha256Digest
    payload: dict[str, JsonValue]
    created_at: AwareDatetime

    @model_validator(mode="after")
    def hash_and_parents_are_valid(self) -> Self:
        ensure_unique(self.parent_artifact_ids, "parent_artifact_ids")
        if self.artifact_id in self.parent_artifact_ids:
            raise ValueError("artifact cannot be its own parent")
        expected = artifact_content_hash(self.payload)
        if self.content_hash != expected:
            raise ValueError("artifact content_hash does not match canonical payload")
        return self

    @classmethod
    def create(
        cls,
        *,
        artifact_id: str,
        run_id: str,
        task_id: str,
        artifact_type: str,
        version: int,
        producer_agent: str,
        payload: Mapping[str, JsonValue],
        created_at: datetime,
        parent_artifact_ids: tuple[str, ...] = (),
        schema_version: str = "1.0",
    ) -> "ArtifactRecord":
        """Create a correctly hashed immutable record from validated JSON data."""

        copied_payload = dict(payload)
        return cls(
            artifact_id=artifact_id,
            run_id=run_id,
            task_id=task_id,
            artifact_type=artifact_type,
            schema_version=schema_version,
            version=version,
            producer_agent=producer_agent,
            parent_artifact_ids=parent_artifact_ids,
            content_hash=artifact_content_hash(copied_payload),
            payload=copied_payload,
            created_at=created_at,
        )

    def to_ref(self) -> ArtifactRef:
        """Return the small message-safe reference passed between tasks."""

        return ArtifactRef(
            artifact_id=self.artifact_id,
            run_id=self.run_id,
            artifact_type=self.artifact_type,
            schema_version=self.schema_version,
            version=self.version,
            content_hash=self.content_hash,
        )


class Blackboard(Protocol):
    """Storage-neutral Artifact API used by workflows and CLI adapters."""

    async def write(
        self, artifact: ArtifactRecord, *, expected_latest_version: int
    ) -> ArtifactRef: ...

    async def get(
        self, *, run_id: str, task_id: str, artifact_type: str, version: int
    ) -> ArtifactRecord | None: ...

    async def latest(
        self, *, run_id: str, task_id: str, artifact_type: str
    ) -> ArtifactRecord | None: ...

    async def lineage(self, artifact_id: str) -> tuple[ArtifactRecord, ...]: ...


class MemoryBlackboard(Blackboard):
    """Lock-protected reference implementation with identical version semantics."""

    def __init__(self) -> None:
        self._records: dict[str, ArtifactRecord] = {}
        self._logical: dict[tuple[str, str, str, int], str] = {}
        self._heads: dict[tuple[str, str, str], int] = {}
        self._lock = asyncio.Lock()

    async def write(self, artifact: ArtifactRecord, *, expected_latest_version: int) -> ArtifactRef:
        async with self._lock:
            head_key = (artifact.run_id, artifact.task_id, artifact.artifact_type)
            current = self._heads.get(head_key, 0)
            if expected_latest_version != current or artifact.version != current + 1:
                raise ArtifactVersionConflictError(
                    f"expected latest version {expected_latest_version}, actual {current}"
                )
            if artifact.artifact_id in self._records:
                raise ArtifactVersionConflictError("artifact_id already exists")
            for parent_id in artifact.parent_artifact_ids:
                parent = self._records.get(parent_id)
                if parent is None:
                    raise ArtifactNotFoundError(f"parent artifact does not exist: {parent_id}")
                if parent.run_id != artifact.run_id:
                    raise ValueError("artifact lineage cannot cross runs")
            logical_key = (*head_key, artifact.version)
            self._records[artifact.artifact_id] = artifact
            self._logical[logical_key] = artifact.artifact_id
            self._heads[head_key] = artifact.version
            return artifact.to_ref()

    async def get(
        self, *, run_id: str, task_id: str, artifact_type: str, version: int
    ) -> ArtifactRecord | None:
        artifact_id = self._logical.get((run_id, task_id, artifact_type, version))
        return None if artifact_id is None else self._records[artifact_id]

    async def latest(
        self, *, run_id: str, task_id: str, artifact_type: str
    ) -> ArtifactRecord | None:
        version = self._heads.get((run_id, task_id, artifact_type))
        if version is None:
            return None
        return await self.get(
            run_id=run_id,
            task_id=task_id,
            artifact_type=artifact_type,
            version=version,
        )

    async def lineage(self, artifact_id: str) -> tuple[ArtifactRecord, ...]:
        if artifact_id not in self._records:
            raise ArtifactNotFoundError(f"artifact does not exist: {artifact_id}")
        ordered: list[ArtifactRecord] = []
        visited: set[str] = set()

        def visit(current_id: str) -> None:
            if current_id in visited:
                return
            current = self._records[current_id]
            for parent_id in current.parent_artifact_ids:
                visit(parent_id)
            visited.add(current_id)
            ordered.append(current)

        visit(artifact_id)
        return tuple(ordered)


class PostgresBlackboard(Blackboard):
    """Transactional implementation using a locked logical Artifact head."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def write(self, artifact: ArtifactRecord, *, expected_latest_version: int) -> ArtifactRef:
        head_key = {
            "run_id": artifact.run_id,
            "task_id": artifact.task_id,
            "artifact_type": artifact.artifact_type,
        }
        async with self.engine.begin() as connection:
            await connection.execute(
                postgres_insert(artifact_heads)
                .values(**head_key, current_version=0, updated_at=artifact.created_at)
                .on_conflict_do_nothing()
            )
            current = (
                await connection.execute(
                    select(artifact_heads.c.current_version)
                    .where(
                        artifact_heads.c.run_id == artifact.run_id,
                        artifact_heads.c.task_id == artifact.task_id,
                        artifact_heads.c.artifact_type == artifact.artifact_type,
                    )
                    .with_for_update()
                )
            ).scalar_one()
            if current != expected_latest_version or artifact.version != current + 1:
                raise ArtifactVersionConflictError(
                    f"expected latest version {expected_latest_version}, actual {current}"
                )
            if artifact.parent_artifact_ids:
                parent_rows = await connection.execute(
                    select(artifacts.c.artifact_id, artifacts.c.run_id).where(
                        artifacts.c.artifact_id.in_(artifact.parent_artifact_ids)
                    )
                )
                parents = {str(row.artifact_id): str(row.run_id) for row in parent_rows}
                missing = set(artifact.parent_artifact_ids) - parents.keys()
                if missing:
                    raise ArtifactNotFoundError(f"parent artifacts do not exist: {sorted(missing)}")
                if any(run_id != artifact.run_id for run_id in parents.values()):
                    raise ValueError("artifact lineage cannot cross runs")

            values = artifact.model_dump(exclude={"parent_artifact_ids"})
            await connection.execute(artifacts.insert().values(**values))
            if artifact.parent_artifact_ids:
                await connection.execute(
                    artifact_parents.insert(),
                    [
                        {
                            "artifact_id": artifact.artifact_id,
                            "parent_artifact_id": parent_id,
                        }
                        for parent_id in artifact.parent_artifact_ids
                    ],
                )
            await connection.execute(
                update(artifact_heads)
                .where(
                    artifact_heads.c.run_id == artifact.run_id,
                    artifact_heads.c.task_id == artifact.task_id,
                    artifact_heads.c.artifact_type == artifact.artifact_type,
                )
                .values(current_version=artifact.version, updated_at=artifact.created_at)
            )
        return artifact.to_ref()

    async def get(
        self, *, run_id: str, task_id: str, artifact_type: str, version: int
    ) -> ArtifactRecord | None:
        statement = select(artifacts).where(
            artifacts.c.run_id == run_id,
            artifacts.c.task_id == task_id,
            artifacts.c.artifact_type == artifact_type,
            artifacts.c.version == version,
        )
        async with self.engine.connect() as connection:
            row = (await connection.execute(statement)).mappings().first()
            return await self._from_row(connection, row)

    async def latest(
        self, *, run_id: str, task_id: str, artifact_type: str
    ) -> ArtifactRecord | None:
        statement = (
            select(artifacts)
            .where(
                artifacts.c.run_id == run_id,
                artifacts.c.task_id == task_id,
                artifacts.c.artifact_type == artifact_type,
            )
            .order_by(artifacts.c.version.desc())
            .limit(1)
        )
        async with self.engine.connect() as connection:
            row = (await connection.execute(statement)).mappings().first()
            return await self._from_row(connection, row)

    async def lineage(self, artifact_id: str) -> tuple[ArtifactRecord, ...]:
        async with self.engine.connect() as connection:
            ordered: list[ArtifactRecord] = []
            visited: set[str] = set()

            async def visit(current_id: str) -> None:
                if current_id in visited:
                    return
                row = (
                    (
                        await connection.execute(
                            select(artifacts).where(artifacts.c.artifact_id == current_id)
                        )
                    )
                    .mappings()
                    .first()
                )
                if row is None:
                    raise ArtifactNotFoundError(f"artifact does not exist: {current_id}")
                record = await self._from_row(connection, row)
                assert record is not None
                for parent_id in record.parent_artifact_ids:
                    await visit(parent_id)
                visited.add(current_id)
                ordered.append(record)

            await visit(artifact_id)
            return tuple(ordered)

    @staticmethod
    async def _from_row(connection: Any, row: RowMapping | None) -> ArtifactRecord | None:
        if row is None:
            return None
        parent_rows = await connection.execute(
            select(artifact_parents.c.parent_artifact_id)
            .where(artifact_parents.c.artifact_id == row["artifact_id"])
            .order_by(artifact_parents.c.parent_artifact_id)
        )
        values = dict(row)
        values["parent_artifact_ids"] = tuple(parent_rows.scalars())
        return ArtifactRecord.model_validate(values)
