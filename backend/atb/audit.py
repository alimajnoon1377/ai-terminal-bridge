"""Append-only, hash-chained audit log.

Each entry commits to its predecessor, so editing or removing an earlier row is
detectable. Truncating the newest rows is only detectable if the latest hash was
recorded somewhere outside the database.
"""

import asyncio
import hashlib
import json

from sqlalchemy import select

from .db import AuditEntry, Database, now

GENESIS = "0" * 64
_FIELDS = ("seq", "ts", "actor_type", "actor_id", "actor_name", "owner_id", "server_id", "action", "status", "detail")


def _digest(prev_hash: str, record: dict) -> str:
    body = json.dumps({key: record[key] for key in _FIELDS}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{prev_hash}\n{body}".encode()).hexdigest()


class AuditLog:
    def __init__(self, database: Database):
        self._db = database
        self._lock = asyncio.Lock()

    async def record(
        self,
        *,
        actor_type: str,
        actor_id: str,
        actor_name: str,
        owner_id: str,
        action: str,
        status: str,
        server_id: str = "",
        detail: dict | None = None,
    ) -> AuditEntry:
        async with self._lock, self._db.session() as session:
            last = await session.scalar(select(AuditEntry).order_by(AuditEntry.seq.desc()).limit(1))
            fields = {
                "seq": (last.seq + 1) if last else 1,
                "ts": now(),
                "actor_type": actor_type,
                "actor_id": actor_id,
                "actor_name": actor_name,
                "owner_id": owner_id,
                "server_id": server_id,
                "action": action,
                "status": status,
                # Round-trip so the hashed form equals what the database returns later.
                "detail": json.loads(json.dumps(detail or {}, default=str)),
            }
            prev_hash = last.hash if last else GENESIS
            entry = AuditEntry(**fields, prev_hash=prev_hash, hash=_digest(prev_hash, fields))
            session.add(entry)
            await session.commit()
            return entry

    async def verify(self) -> dict:
        prev_hash, expected_seq = GENESIS, 1
        async with self._db.session() as session:
            result = await session.stream_scalars(select(AuditEntry).order_by(AuditEntry.seq))
            async for entry in result:
                record = {key: getattr(entry, key) for key in _FIELDS}
                if entry.seq != expected_seq or entry.prev_hash != prev_hash or entry.hash != _digest(prev_hash, record):
                    return {"valid": False, "broken_at": entry.seq, "entries": expected_seq - 1}
                prev_hash, expected_seq = entry.hash, expected_seq + 1
        return {"valid": True, "entries": expected_seq - 1, "head": prev_hash}
