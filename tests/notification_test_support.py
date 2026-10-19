"""Isolated registration state and deterministic lock scheduling; no network/DB."""
import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4


class RegistrationDatabase:
    def __init__(self, record):
        self.records = {record["id"]: dict(record)}
        self.lock = asyncio.Lock()
        self.guard_entered = asyncio.Event()
        self.writer_waiting = asyncio.Event()
        self.guard_waiting = asyncio.Event()
        self.cleaned = 0

    @asynccontextmanager
    async def acquire(self):
        yield RegistrationConnection(self)

    async def fetch(self, query, user_id):
        assert "mobile_devices" in query
        return [dict(row) for row in self.records.values() if row["user_id"] == user_id and row["enabled"]]

    async def fetchrow(self, query, *args):
        if "INSERT INTO notifications" in query:
            return {"id": uuid4()}
        assert "INSERT INTO mobile_devices" in query
        self.writer_waiting.set()
        async with self.lock:
            row = next((row for row in self.records.values() if (row["provider"], row["token"]) == (args[1], args[2])), None)
            if row is None:
                row = {"id": uuid4(), "created_at": datetime.now(UTC)}
                self.records[row["id"]] = row
            previous = row.get("last_seen_at", datetime.now(UTC))
            row.update(user_id=args[0], provider=args[1], token=args[2], platform=args[3], enabled=True,
                       last_seen_at=previous + timedelta(microseconds=1))
            return {key: value for key, value in row.items() if key != "token"}

    async def execute(self, query, *args):
        if "INSERT INTO audit_records" in query:
            return
        assert "UPDATE mobile_devices" in query
        self.writer_waiting.set()
        async with self.lock:
            for row in self.records.values():
                if "last_seen_at = $5" in query:
                    matches = (row["id"], row["user_id"], row["provider"], row["token"], row["last_seen_at"]) == args
                else:
                    matches = (row["user_id"], row["provider"], row["token"]) == args
                if matches:
                    row["enabled"] = False
                    self.cleaned += 1


class RegistrationConnection:
    def __init__(self, database):
        self.database = database
        self.held = False

    @asynccontextmanager
    async def transaction(self, *, isolation):
        assert isolation == "read_committed"
        try:
            yield
        finally:
            if self.held:
                self.database.lock.release()

    async def fetchval(self, query, *snapshot):
        assert "FOR SHARE" in query and "enabled = true" in query
        self.database.guard_waiting.set()
        await self.database.lock.acquire()
        self.held = True
        self.database.guard_entered.set()
        row = self.database.records.get(snapshot[0])
        if row and row["enabled"] and (row["id"], row["user_id"], row["provider"], row["token"], row["last_seen_at"]) == snapshot:
            return row["id"]
        return None
