from __future__ import annotations

import unittest
from datetime import date, time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException

import app.main as main


class Record(dict):
    def keys(self):
        return super().keys()


class RegressionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.original_environment = main.settings.environment
        self.original_expose_codes = main.settings.expose_development_verification_codes
        self.original_email_provider = main.settings.email_provider
        self.original_agentmail_api_key = main.settings.agentmail_api_key
        self.original_agentmail_from_email = main.settings.agentmail_from_email
        self.original_smtp_host = main.settings.smtp_host
        self.original_smtp_from_email = main.settings.smtp_from_email
        self.original_smtp_use_tls = main.settings.smtp_use_tls

    def tearDown(self) -> None:
        main.settings.environment = self.original_environment
        main.settings.expose_development_verification_codes = self.original_expose_codes
        main.settings.email_provider = self.original_email_provider
        main.settings.agentmail_api_key = self.original_agentmail_api_key
        main.settings.agentmail_from_email = self.original_agentmail_from_email
        main.settings.smtp_host = self.original_smtp_host
        main.settings.smtp_from_email = self.original_smtp_from_email
        main.settings.smtp_use_tls = self.original_smtp_use_tls

    async def test_production_register_endpoint_requires_verified_flow(self):
        main.settings.environment = "production"
        main.settings.expose_development_verification_codes = False
        payload = main.RegisterIn(email="parent@example.com", password="password123", name="Parent")

        with self.assertRaises(HTTPException) as raised:
            await main.register(payload, MagicMock())

        self.assertEqual(raised.exception.status_code, 410)

    def test_verification_codes_are_not_exposed_in_production(self):
        main.settings.environment = "production"
        main.settings.expose_development_verification_codes = True

        payload = main.delivery_payload("parent@example.com", "sent", "123456", "registration_token")

        self.assertIsNone(payload["verification_code"])
        self.assertIsNone(payload["registration_token"])

    def test_production_refuses_unsent_verification_email_without_code_leak(self):
        main.settings.environment = "production"
        main.settings.expose_development_verification_codes = False

        with self.assertRaises(HTTPException) as raised:
            main.delivery_payload("parent@example.com", "not_configured", "123456")

        self.assertEqual(raised.exception.status_code, 503)

    async def test_agentmail_and_smtp_share_send_email_service(self):
        main.settings.email_provider = "agentmail"
        main.settings.agentmail_api_key = "key"
        main.settings.agentmail_from_email = "sender@example.com"
        response = SimpleNamespace(status_code=202)
        client = AsyncMock()
        client.post.return_value = response
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None

        with patch.object(main.httpx, "AsyncClient", return_value=client):
            self.assertEqual(await main.send_email("to@example.com", "Subject", "Body"), "sent")
            client.post.assert_awaited_once()

        main.settings.email_provider = "smtp"
        main.settings.smtp_host = "smtp.example.com"
        main.settings.smtp_from_email = "sender@example.com"
        main.settings.smtp_use_tls = True
        smtp = MagicMock()
        smtp.__enter__.return_value = smtp
        smtp.__exit__.return_value = None

        with patch.object(main.smtplib, "SMTP", return_value=smtp):
            self.assertEqual(await main.send_email("to@example.com", "Subject", "Body"), "sent")
            smtp.starttls.assert_called_once()
            smtp.send_message.assert_called_once()

    async def test_one_user_cannot_join_multiple_active_groups(self):
        current_group_id = uuid4()
        other_group_id = uuid4()
        database = AsyncMock()
        database.fetchval.return_value = current_group_id

        await main.require_no_active_group(database, uuid4(), current_group_id)
        with self.assertRaises(HTTPException) as raised:
            await main.require_no_active_group(database, uuid4(), other_group_id)

        self.assertEqual(raised.exception.status_code, 409)

    async def test_current_future_roster_and_history_filtering(self):
        user_id = uuid4()
        group_id = uuid4()
        driver = Record({"id": user_id, "name": "Driver", "email": "driver@example.com", "phone": None})
        today = date.today()
        planned_today = self.trip(group_id, user_id, today, "planned")
        delayed_future = self.trip(group_id, user_id, date(today.year + 1, 1, 1), "delayed")
        completed_today = self.trip(group_id, user_id, today, "completed")
        cancelled_future = self.trip(group_id, user_id, date(today.year + 1, 1, 2), "cancelled")
        past_planned = self.trip(group_id, user_id, date(2020, 1, 1), "planned")
        database = TripListDatabase(
            user_id=user_id,
            group_id=group_id,
            trips=[planned_today, delayed_future, completed_today, cancelled_future, past_planned],
            driver=driver,
        )
        user = {"id": str(user_id)}

        current = await main.list_trips(group_id, user, database)
        history = await main.list_trip_history(group_id, user, database)

        self.assertEqual([trip["id"] for trip in current], [str(planned_today["id"]), str(delayed_future["id"])])
        self.assertEqual(
            [trip["id"] for trip in history],
            [str(cancelled_future["id"]), str(completed_today["id"]), str(past_planned["id"])],
        )

    async def test_idempotent_notification_sends_email_once(self):
        user_id = uuid4()
        entity_id = uuid4()
        database = NotificationDatabase(user_id)

        with patch.object(main, "deliver_push", new=AsyncMock()) as push, patch.object(main, "send_email", new=AsyncMock()) as email:
            await main.notify(database, user_id, "handover", "Title", "Body", "trip", entity_id, email=True, idempotency_key="handover:key")
            await main.notify(database, user_id, "handover", "Title", "Body", "trip", entity_id, email=True, idempotency_key="handover:key")

        self.assertEqual(database.inserted_notifications, 1)
        push.assert_awaited_once()
        email.assert_awaited_once_with("parent@example.com", "Title", "Body")

    def trip(self, group_id, user_id, service_date, status):
        return Record(
            {
                "id": uuid4(),
                "group_id": group_id,
                "driver_user_id": user_id,
                "service_date": service_date,
                "trip_type": "pickup",
                "expected_time": time(8, 0),
                "status": status,
                "delay_minutes": 0,
                "delay_note": None,
                "cancellation_reason": None,
                "started_at": None,
                "ended_at": None,
                "safety_timeout_at": None,
                "created_by": user_id,
                "created_at": None,
            }
        )


class NotificationDatabase:
    def __init__(self, user_id):
        self.user_id = user_id
        self.seen_keys = set()
        self.inserted_notifications = 0

    async def fetchrow(self, query, *args):
        if "INSERT INTO notifications" in query:
            key = args[-1]
            if key in self.seen_keys:
                return None
            self.seen_keys.add(key)
            self.inserted_notifications += 1
            return Record({"id": uuid4()})
        if "SELECT email FROM users" in query:
            return Record({"email": "parent@example.com"})
        raise AssertionError(query)

    async def fetch(self, query, *args):
        if "mobile_devices" in query:
            return []
        raise AssertionError(query)


class TripListDatabase:
    def __init__(self, user_id, group_id, trips, driver):
        self.user_id = user_id
        self.group_id = group_id
        self.trips = trips
        self.driver = driver

    async def fetchrow(self, query, *args):
        if "FROM users WHERE id" in query:
            return self.driver
        if "FROM trip_responses" in query or "FROM trip_locations" in query:
            return None
        if "FROM group_members" in query:
            return Record({"group_id": self.group_id, "user_id": self.user_id, "role": "creator", "status": "active"})
        raise AssertionError(query)

    async def fetchval(self, query, *args):
        if "SELECT created_by FROM carpool_groups" in query:
            return self.user_id
        raise AssertionError(query)

    async def fetch(self, query, *args):
        if "FROM trip_children" in query and "JOIN children" in query:
            return []
        if "FROM trips" in query and "CURRENT_DATE" in query:
            rows = [trip for trip in self.trips if trip["service_date"] < date.today() or trip["status"] in {"completed", "cancelled", "driver_unavailable"}]
            return sorted(rows, key=lambda trip: (trip["service_date"], trip["expected_time"]), reverse=True)
        if "FROM trips" in query:
            from_date = args[1]
            to_date = args[2]
            rows = [
                trip
                for trip in self.trips
                if trip["service_date"] >= from_date
                and (to_date is None or trip["service_date"] <= to_date)
                and trip["status"] not in {"completed", "cancelled", "driver_unavailable"}
            ]
            return sorted(rows, key=lambda trip: (trip["service_date"], trip["expected_time"]))
        raise AssertionError(query)


if __name__ == "__main__":
    unittest.main()
