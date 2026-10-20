import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
import app.main as main
from test_regressions import ActionIds, TripActionDatabase, TripListDatabase, Record


class HandoverActionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ids = ActionIds()
        self.db = TripActionDatabase(self.ids)
        self.user = {"id": str(self.ids.driver_id)}

    async def submit(self, action, note=None):
        return await main.handover(self.ids.trip_id, self.ids.child_id, main.HandoverIn(handover_type=action, note=note), self.user, self.db)

    async def test_each_trip_type_accepts_only_its_action_and_notifies_parent(self):
        for trip_type, right, wrong in [("pickup", "picked_up", "dropped_off"), ("dropoff", "dropped_off", "picked_up")]:
            with self.subTest(trip_type=trip_type):
                self.setUp()
                self.db.trip["trip_type"] = trip_type
                with self.assertRaises(HTTPException) as error:
                    await self.submit(wrong)
                self.assertEqual(error.exception.status_code, 409)
                self.assertEqual(self.db.handover_updates, 0)
                self.assertEqual(self.db.notifications, [])
                result = await self.submit(right)
                self.assertEqual(result["handover_type"], right)
                self.assertEqual(len(self.db.notifications), 1)
                self.assertEqual(self.db.notifications[0]["user_id"], self.ids.parent_id)

    async def test_duplicate_preserves_original_record_note_and_notifies_once(self):
        with patch.object(main, "deliver_push", new=AsyncMock()) as push:
            first = await self.submit("picked_up", "Original note")
            second = await self.submit("picked_up", "Changed note")
        self.assertEqual(first, second)
        self.assertEqual(second["note"], "Original note")
        self.assertEqual(self.db.handover_updates, 1)
        self.assertEqual(len(self.db.notifications), 1)
        push.assert_awaited_once()
        self.assertEqual(sum(action == "trip.handover" for action, _ in self.db.audit_actions), 1)

    async def test_absence_cannot_overwrite_completed_handover(self):
        await self.submit("picked_up")
        with self.assertRaises(HTTPException) as error:
            await self.submit("absent")
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.db.child_status["pickup_status"], "picked_up")

    async def test_dropoff_absence_updates_correct_status_only(self):
        self.db.trip["trip_type"] = "dropoff"
        await self.submit("absent")
        self.assertEqual(self.db.child_status, {"pickup_status": "pending", "dropoff_status": "absent"})

    async def test_legacy_dropoff_absence_blocks_completion_and_preserves_original_event(self):
        self.db.trip["trip_type"] = "dropoff"
        original = await self.submit("absent", "Original absence")
        # State produced by the pre-release API: absence always wrote pickup_status.
        self.db.child_status = {"pickup_status": "absent", "dropoff_status": "pending"}
        with self.assertRaises(HTTPException) as error:
            await self.submit("dropped_off")
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(await self.submit("absent", "Replacement note"), original)
        self.assertEqual(set(self.db.handovers), {"absent"})
        self.assertEqual(self.db.child_status["dropoff_status"], "pending")
        self.assertEqual(len(self.db.audit_actions), 1)

    async def test_notification_failure_after_commit_does_not_lose_or_duplicate_audit(self):
        with patch.object(main, "notify", new=AsyncMock(side_effect=RuntimeError("synthetic notification failure"))):
            with self.assertRaises(RuntimeError):
                await self.submit("picked_up")
        self.assertEqual(len(self.db.handovers), 1)
        self.assertEqual(len(self.db.audit_actions), 1)
        with patch.object(main, "notify", new=AsyncMock()):
            await self.submit("picked_up")
        self.assertEqual(len(self.db.audit_actions), 1)

    def test_legacy_absence_normalization_keeps_existing_client_shape_and_source_row(self):
        for trip_type, column in [("pickup", "pickup_status"), ("dropoff", "dropoff_status")]:
            child = {"child_id": "synthetic", "pickup_status": "absent", "dropoff_status": "pending", "handover_absent": True}
            payload = main.normalize_handover_status(child, trip_type)
            self.assertEqual(payload[column], "absent")
            self.assertNotIn("handover_absent", payload)
            self.assertEqual(child["dropoff_status"], "pending")

    async def test_single_trip_children_normalizes_legacy_absence_without_exposing_internal_field(self):
        self.db.trip["trip_type"] = "dropoff"
        original_fetch = self.db.fetch
        async def fetch(query, *args):
            rows = await original_fetch(query, *args)
            if "FROM trip_children" in query and "JOIN children" in query:
                self.assertIn("AS handover_absent", query)
                return [{**row, "pickup_status": "absent", "dropoff_status": "pending", "handover_absent": True} for row in rows]
            return rows
        with patch.object(self.db, "fetch", new=fetch):
            children = await main.visible_trip_children(self.db, self.user["id"], self.db.trip)
        self.assertEqual(children[0]["dropoff_status"], "absent")
        self.assertNotIn("handover_absent", children[0])

    async def test_trip_list_normalizes_legacy_absence_with_existing_response_fields(self):
        trip = {**self.db.trip, "trip_type": "dropoff"}
        driver = Record({"id": self.ids.driver_id, "name": "Driver", "email": "driver@example.test", "phone": None})
        database = TripListDatabase(self.ids.driver_id, self.ids.group_id, [trip], driver)
        original_fetch = database.fetch
        async def fetch(query, *args):
            if "FROM trip_children" in query and "JOIN children" in query:
                self.assertIn("AS handover_absent", query)
                return [Record({"trip_id": self.ids.trip_id, "child_id": self.ids.child_id, "parent_id": self.ids.parent_id,
                                "name": "Child", "pickup_status": "absent", "dropoff_status": "pending", "handover_absent": True})]
            return await original_fetch(query, *args)
        with patch.object(database, "fetch", new=fetch):
            trips = await main.serialize_trips(database, self.ids.driver_id, [trip])
        child = trips[0]["children"][0]
        self.assertEqual(child["dropoff_status"], "absent")
        self.assertNotIn("handover_absent", child)
