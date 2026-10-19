import asyncio
import unittest
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from fastapi import HTTPException

import app.main as main
from notification_test_support import RegistrationDatabase
from test_regressions import ActionIds, TripActionDatabase


class RegistrationConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.user_id = uuid4()
        self.device = {"id": uuid4(), "user_id": self.user_id, "provider": "fcm", "token": "synthetic-test-device",
                       "platform": "android", "enabled": True, "last_seen_at": datetime.now(UTC)}
        self.database = RegistrationDatabase(self.device)
        self.client = AsyncMock()
        self.client.__aenter__.return_value = self.client
        self.client.post.return_value = self.response(200)
        for patcher in [patch.object(main.settings, "fcm_service_account_path", "/fake/credentials.json"),
                        patch.object(main, "fcm_access_token", new=AsyncMock(return_value="synthetic-access")),
                        patch.object(main.httpx, "AsyncClient", return_value=self.client),
                        patch.object(main.secrets, "randbelow", return_value=0)]:
            patcher.start()
            self.addCleanup(patcher.stop)

    def response(self, status):
        return httpx.Response(status, json={"error": {"details": []}}, request=httpx.Request("POST", "https://fcm.googleapis.com/send"))

    async def deliver(self):
        await main.deliver_push(self.database, self.user_id, "handover", "Synthetic child", "Synthetic note", "trip", uuid4())

    async def change_registration(self, kind):
        if kind == "logout":
            await main.unregister_mobile_device(main.MobileDeviceIn(provider="fcm", token=self.device["token"]), {"id": str(self.user_id)}, self.database)
        else:
            owner = uuid4() if kind == "reassignment" else self.user_id
            await main.register_mobile_device(main.MobileDeviceIn(provider="fcm", token=self.device["token"], platform="android"), {"id": str(owner)}, self.database)

    async def test_original_reassignment_reproduction_and_logout_refresh_cancel_retry(self):
        for kind in ["reassignment", "logout", "refresh"]:
            with self.subTest(kind=kind):
                self.database = RegistrationDatabase(self.device)
                self.client.post.reset_mock()
                self.client.post.side_effect = [self.response(503), self.response(200)]
                async def change_during_backoff(*args):
                    self.assertFalse(self.database.lock.locked())
                    await self.change_registration(kind)
                with patch.object(main.asyncio, "sleep", side_effect=change_during_backoff):
                    await self.deliver()
                self.client.post.assert_awaited_once()
                self.assertEqual(self.database.cleaned, 1 if kind == "logout" else 0)

    async def test_every_snapshot_field_and_active_status_are_checked_before_first_send(self):
        changes = {"id": uuid4(), "user_id": uuid4(), "token": "refreshed-token", "provider": "apns",
                   "last_seen_at": self.device["last_seen_at"] + timedelta(microseconds=1), "enabled": False}
        for field, changed in changes.items():
            with self.subTest(field=field):
                self.database = RegistrationDatabase(self.device)
                snapshot = dict(self.device)
                self.database.records[self.device["id"]][field] = changed
                self.client.post.reset_mock()
                # Simulate mutation after the initial device lookup, before first POST.
                with patch.object(self.database, "fetch", new=AsyncMock(return_value=[snapshot])):
                    await self.deliver()
                self.client.post.assert_not_awaited()

    async def test_reassignment_waits_until_in_flight_attempt_finishes_then_cancels_retry(self):
        entered, finish = asyncio.Event(), asyncio.Event()
        async def post(*args, **kwargs):
            self.assertTrue(self.database.lock.locked())
            entered.set()
            await finish.wait()
            return self.response(503)
        self.client.post.side_effect = post
        async with asyncio.timeout(2):
            writer = None
            async def finish_writer_during_backoff(*args):
                self.assertFalse(self.database.lock.locked())
                await writer
            with patch.object(main.asyncio, "sleep", side_effect=finish_writer_during_backoff):
                delivery = asyncio.create_task(self.deliver())
                await entered.wait()
                writer = asyncio.create_task(self.change_registration("reassignment"))
                await self.database.writer_waiting.wait()
                self.assertFalse(writer.done())
                finish.set()
                await asyncio.gather(delivery, writer)
        self.client.post.assert_awaited_once()

    async def test_successful_in_flight_delivery_cannot_be_retracted_by_logout(self):
        entered, finish = asyncio.Event(), asyncio.Event()
        async def post(*args, **kwargs):
            entered.set()
            await finish.wait()
            return self.response(200)
        self.client.post.side_effect = post
        async with asyncio.timeout(2):
            delivery = asyncio.create_task(self.deliver())
            await entered.wait()
            logout = asyncio.create_task(self.change_registration("logout"))
            await self.database.writer_waiting.wait()
            self.assertFalse(logout.done())
            finish.set()
            await asyncio.gather(delivery, logout)
        self.client.post.assert_awaited_once()
        self.assertFalse(self.database.records[self.device["id"]]["enabled"])

    async def test_refresh_winning_lock_prevents_first_send(self):
        async with self.database.lock:
            delivery = asyncio.create_task(self.deliver())
            await self.database.guard_waiting.wait()
            self.database.records[self.device["id"]]["last_seen_at"] += timedelta(microseconds=1)
        await asyncio.wait_for(delivery, 2)
        self.client.post.assert_not_awaited()

    async def test_validation_database_error_fails_closed(self):
        with patch.object(self.database, "acquire", side_effect=main.asyncpg.InterfaceError("synthetic DB failure")):
            await self.deliver()
        self.client.post.assert_not_awaited()

    async def test_unguarded_fcm_send_fails_closed(self):
        with self.assertRaises(ValueError):
            await main.send_push_to_device(self.device, "Title", "Body", {})
        self.client.post.assert_not_awaited()


class ConcurrentSwapDatabase(TripActionDatabase):
    def __init__(self, ids):
        super().__init__(ids)
        self.trip_lock = asyncio.Lock()
        self.two_lock_attempts = asyncio.Event()
        self.lock_attempts = 0

    @asynccontextmanager
    async def acquire(self):
        yield ConcurrentSwapConnection(self)

    async def fetchrow(self, query, *args):
        result = await super().fetchrow(query, *args)
        if "INSERT INTO roster_change_requests" in query:
            self.open_swap_exists = True
        return result

    async def fetch(self, query, *args):
        if "mobile_devices" in query:
            return [{"id": uuid4(), "user_id": args[0], "provider": "fcm", "token": "synthetic-only", "last_seen_at": datetime.now(UTC)}]
        return await super().fetch(query, *args)


class ConcurrentSwapConnection:
    def __init__(self, database):
        self.database = database
        self.locked = False

    def __getattr__(self, name):
        return getattr(self.database, name)

    @asynccontextmanager
    async def transaction(self, *, isolation):
        assert isolation == "read_committed"
        try:
            yield
        finally:
            if self.locked:
                self.database.trip_lock.release()

    async def fetchrow(self, query, *args):
        if "FOR UPDATE" in query and "FROM trips" in query:
            self.database.lock_attempts += 1
            if self.database.lock_attempts == 2:
                self.database.two_lock_attempts.set()
            await self.database.trip_lock.acquire()
            self.locked = True
        return await self.database.fetchrow(query, *args)

    async def fetchval(self, query, *args):
        if "FROM roster_change_requests" in query and "status = 'open'" in query:
            await self.database.two_lock_attempts.wait()
        return await self.database.fetchval(query, *args)


class SwapConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_simultaneous_swap_requests_dispatch_only_the_winning_event(self):
        for different_replacement in [False, True]:
            with self.subTest(different_replacement=different_replacement):
                ids = ActionIds()
                database = ConcurrentSwapDatabase(ids)
                payloads = [main.ChangeRequestIn(request_type="swap", proposed_driver_user_id=replacement, note="Synthetic request")
                            for replacement in [ids.proposed_driver_id, uuid4() if different_replacement else ids.proposed_driver_id]]
                async with asyncio.timeout(2):
                    with patch.object(main, "send_push_to_device", new=AsyncMock()) as send:
                        results = await asyncio.gather(*(main.request_roster_change(ids.trip_id, payload, {"id": str(ids.driver_id), "name": "Synthetic driver"}, database) for payload in payloads), return_exceptions=True)
                self.assertEqual(sum(isinstance(row, dict) for row in results), 1, results)
                self.assertEqual(sum(isinstance(row, HTTPException) and row.status_code == 409 for row in results), 1, results)
                self.assertEqual(send.await_count, 2)
                self.assertEqual(len(database.notifications), 2)
