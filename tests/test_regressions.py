from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta, time
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
        self.original_google_routes_api_key = main.settings.google_routes_api_key
        self.original_location_stale_seconds = main.settings.location_stale_seconds

    def tearDown(self) -> None:
        main.settings.environment = self.original_environment
        main.settings.expose_development_verification_codes = self.original_expose_codes
        main.settings.email_provider = self.original_email_provider
        main.settings.agentmail_api_key = self.original_agentmail_api_key
        main.settings.agentmail_from_email = self.original_agentmail_from_email
        main.settings.smtp_host = self.original_smtp_host
        main.settings.smtp_from_email = self.original_smtp_from_email
        main.settings.smtp_use_tls = self.original_smtp_use_tls
        main.settings.google_routes_api_key = self.original_google_routes_api_key
        main.settings.location_stale_seconds = self.original_location_stale_seconds

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
        ignored_future = self.trip(group_id, user_id, date(today.year + 1, 1, 3), "ignored")
        past_planned = self.trip(group_id, user_id, date(2020, 1, 1), "planned")
        database = TripListDatabase(
            user_id=user_id,
            group_id=group_id,
            trips=[planned_today, delayed_future, completed_today, cancelled_future, ignored_future, past_planned],
            driver=driver,
        )
        user = {"id": str(user_id)}

        current = await main.list_trips(group_id, user, database)
        history = await main.list_trip_history(group_id, user, database)

        self.assertEqual([trip["id"] for trip in current], [str(planned_today["id"]), str(delayed_future["id"])])
        self.assertEqual(
            [trip["id"] for trip in history],
            [str(ignored_future["id"]), str(cancelled_future["id"]), str(completed_today["id"]), str(past_planned["id"])],
        )

    async def test_trip_list_uses_batched_serialization_queries(self):
        user_id = uuid4()
        group_id = uuid4()
        driver = Record({"id": user_id, "name": "Driver", "email": "driver@example.com", "phone": None})
        trips = [self.trip(group_id, user_id, date.today() + timedelta(days=index), "planned") for index in range(4)]
        database = TripListDatabase(user_id=user_id, group_id=group_id, trips=trips, driver=driver)

        await main.list_trips(group_id, {"id": str(user_id)}, database)

        self.assertEqual(sum("FROM users WHERE id = ANY" in query for query in database.fetch_queries), 1)
        self.assertEqual(sum("FROM trip_locations" in query for query in database.fetch_queries), 1)

    async def test_route_eta_unavailable_before_trip_starts(self):
        ids = ActionIds()
        database = RouteDatabase(ids, status="planned")

        result = await main.trip_route(ids.trip_id, {"id": str(ids.parent_id)}, database)

        self.assertIsNone(result["route"])
        self.assertEqual(result["message"], "Live ETA is available only during an active trip")

    async def test_route_eta_available_after_start_with_driver_gps(self):
        ids = ActionIds()
        main.settings.google_routes_api_key = "routes-key"
        database = RouteDatabase(ids, trip_type="pickup")
        response = MagicMock()
        response.json.return_value = {"routes": [{"duration": "720s", "distanceMeters": 3200}]}
        client = AsyncMock()
        client.post.return_value = response
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None

        with patch.object(main.httpx, "AsyncClient", return_value=client):
            result = await main.trip_route(ids.trip_id, {"id": str(ids.parent_id)}, database)

        self.assertEqual(result["route"]["target_label"], "school")
        self.assertEqual(result["route"]["duration_seconds"], 720)
        self.assertIsNotNone(result["route"]["eta_at"])
        request_body = client.post.await_args.kwargs["json"]
        self.assertEqual(request_body["destination"], {"address": "1 School St, Berlin, DE"})

    async def test_route_eta_uses_parent_child_home_destination_for_dropoff(self):
        ids = ActionIds()
        main.settings.google_routes_api_key = "routes-key"
        database = RouteDatabase(ids, trip_type="dropoff")
        response = MagicMock()
        response.json.return_value = {"routes": [{"duration": "420s", "distanceMeters": 1500}]}
        client = AsyncMock()
        client.post.return_value = response
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None

        with patch.object(main.httpx, "AsyncClient", return_value=client):
            result = await main.trip_route(ids.trip_id, {"id": str(ids.parent_id)}, database)

        self.assertEqual(result["route"]["scope"], "your_child")
        self.assertEqual(result["route"]["target_label"], "your drop-off stop")
        self.assertEqual(result["route"]["stops"], [{"child_id": str(ids.child_id), "name": "Child", "address": "1 Home St, Berlin, DE"}])
        request_body = client.post.await_args.kwargs["json"]
        self.assertEqual(request_body["destination"], {"address": "1 Home St, Berlin, DE"})

    async def test_route_eta_rejects_unauthorized_user(self):
        ids = ActionIds()
        database = RouteDatabase(ids, parent_allowed=False)

        with self.assertRaises(HTTPException) as raised:
            await main.trip_route(ids.trip_id, {"id": str(ids.unrelated_id)}, database)

        self.assertEqual(raised.exception.status_code, 403)

    async def test_route_eta_unavailable_when_driver_gps_missing_or_stale(self):
        ids = ActionIds()
        database = RouteDatabase(ids, latest_location=None)

        missing = await main.trip_route(ids.trip_id, {"id": str(ids.parent_id)}, database)

        self.assertIsNone(missing["route"])
        self.assertEqual(missing["message"], "Waiting for driver's location...")

        main.settings.location_stale_seconds = 90
        stale_database = RouteDatabase(ids, latest_location_received_at=datetime.now(UTC) - timedelta(minutes=5))

        stale = await main.trip_route(ids.trip_id, {"id": str(ids.parent_id)}, stale_database)

        self.assertIsNone(stale["route"])
        self.assertIn("ETA temporarily unavailable", stale["message"])

    async def test_completed_trip_does_not_return_live_eta(self):
        ids = ActionIds()
        database = RouteDatabase(ids, status="completed", ended_at=datetime.now(UTC))

        result = await main.trip_route(ids.trip_id, {"id": str(ids.parent_id)}, database)

        self.assertIsNone(result["route"])
        self.assertEqual(result["message"], "Live ETA is available only during an active trip")

    async def test_driver_10_minute_delay_notifies_only_affected_recipients(self):
        ids = ActionIds()
        database = TripActionDatabase(ids)
        user = {"id": str(ids.driver_id), "name": "Driver"}
        payload = main.TripNoteIn(minutes=10, note="Driver reported a 10-minute delay.")

        updated = await main.delay_trip(ids.trip_id, payload, user, database)

        self.assertEqual(updated["status"], "delayed")
        notified = {(row["user_id"], row["kind"]) for row in database.notifications}
        self.assertEqual(
            notified,
            {
                (ids.parent_id, "delay"),
                (ids.organiser_id, "delay"),
            },
        )
        self.assertNotIn((ids.unrelated_id, "delay"), notified)
        self.assertIn(("trip.delayed", ids.trip_id), database.audit_actions)

    async def test_trip_swap_request_notifies_driver_and_organiser_only(self):
        ids = ActionIds()
        database = TripActionDatabase(ids)
        user = {"id": str(ids.driver_id), "name": "Driver"}
        payload = main.ChangeRequestIn(
            request_type="swap",
            proposed_driver_user_id=ids.proposed_driver_id,
            note="Assigned driver requested a trip swap.",
        )

        row = await main.request_roster_change(ids.trip_id, payload, user, database)

        self.assertEqual(row["request_type"], "swap")
        notified = {(row["user_id"], row["kind"]) for row in database.notifications}
        self.assertEqual(
            notified,
            {
                (ids.organiser_id, "trip_swap_request"),
                (ids.proposed_driver_id, "trip_swap_request"),
            },
        )
        self.assertNotIn((ids.unrelated_id, "trip_swap_request"), notified)
        self.assertIn(("trip.change_requested", ids.trip_id), database.audit_actions)

    async def test_pending_actions_include_received_open_swap(self):
        ids = ActionIds()
        database = PendingActionsDatabase(ids, swap_status="open", swap_proposed_user_id=ids.proposed_driver_id)
        user = {"id": str(ids.proposed_driver_id), "email": "driver-b@example.com"}

        actions = await main.list_pending_actions(user, database)

        self.assertEqual([action["type"] for action in actions], ["swap_request"])

    async def test_pending_actions_exclude_sent_swap(self):
        ids = ActionIds()
        database = PendingActionsDatabase(
            ids,
            assignment_response_status="accepted",
            swap_status="open",
            swap_proposed_user_id=ids.proposed_driver_id,
        )
        user = {"id": str(ids.driver_id), "email": "driver-a@example.com"}

        actions = await main.list_pending_actions(user, database)

        self.assertEqual(actions, [])

    async def test_pending_actions_exclude_accepted_swap(self):
        ids = ActionIds()
        database = PendingActionsDatabase(ids, swap_status="accepted", swap_proposed_user_id=ids.proposed_driver_id)
        user = {"id": str(ids.proposed_driver_id), "email": "driver-b@example.com"}

        actions = await main.list_pending_actions(user, database)

        self.assertEqual(actions, [])

    async def test_pending_actions_exclude_declined_swap(self):
        ids = ActionIds()
        database = PendingActionsDatabase(ids, swap_status="declined", swap_proposed_user_id=ids.proposed_driver_id)
        user = {"id": str(ids.proposed_driver_id), "email": "driver-b@example.com"}

        actions = await main.list_pending_actions(user, database)

        self.assertEqual(actions, [])

    async def test_pending_actions_exclude_accepted_driver_assignment(self):
        ids = ActionIds()
        database = PendingActionsDatabase(ids, assignment_response_status="accepted")
        user = {"id": str(ids.driver_id), "email": "driver-a@example.com"}

        actions = await main.list_pending_actions(user, database)

        self.assertEqual(actions, [])

    async def test_pending_actions_exclude_declined_driver_assignment(self):
        ids = ActionIds()
        database = PendingActionsDatabase(ids, assignment_response_status="declined")
        user = {"id": str(ids.driver_id), "email": "driver-a@example.com"}

        actions = await main.list_pending_actions(user, database)

        self.assertEqual(actions, [])

    async def test_duplicate_delay_notifications_are_prevented(self):
        ids = ActionIds()
        database = TripActionDatabase(ids)
        user = {"id": str(ids.driver_id), "name": "Driver"}
        payload = main.TripNoteIn(minutes=10, note="Driver reported a 10-minute delay.")

        await main.delay_trip(ids.trip_id, payload, user, database)
        await main.delay_trip(ids.trip_id, payload, user, database)

        delay_notifications = [row for row in database.notifications if row["kind"] == "delay"]
        self.assertEqual(len(delay_notifications), 2)
        self.assertEqual({row["user_id"] for row in delay_notifications}, {ids.parent_id, ids.organiser_id})

    async def test_individual_driver_notification_for_assignment_uses_idempotency(self):
        user_id = uuid4()
        trip_id = uuid4()
        database = NotificationDatabase(user_id)

        with patch.object(main, "deliver_push", new=AsyncMock()) as push, patch.object(main, "send_email", new=AsyncMock()) as email:
            await main.notify(
                database,
                user_id,
                "driver_assignment",
                "SchoolHop driver assignment",
                "You were assigned to drive a carpool trip.",
                "trip",
                trip_id,
                email=True,
                idempotency_key=f"driver_assignment:{trip_id}:{user_id}",
            )

        self.assertEqual(database.inserted_notifications, 1)
        push.assert_awaited_once()
        email.assert_not_awaited()

    async def test_individual_parent_notification_for_handover_uses_child_recipient(self):
        ids = ActionIds()
        database = TripActionDatabase(ids)
        user = {"id": str(ids.driver_id), "name": "Driver"}
        payload = main.HandoverIn(handover_type="picked_up")

        await main.handover(ids.trip_id, ids.child_id, payload, user, database)

        self.assertEqual([(row["user_id"], row["kind"]) for row in database.notifications], [(ids.parent_id, "handover")])
        self.assertNotIn(ids.unrelated_id, {row["user_id"] for row in database.notifications})

    async def test_invitation_notification_includes_code_for_registered_invitee(self):
        ids = ActionIds()
        database = InvitationDatabase(ids)
        user = {"id": str(ids.organiser_id), "name": "Organiser"}
        payload = main.InviteIn(email="invitee@example.com")

        with patch.object(main.secrets, "token_urlsafe", return_value="fixed-token"):
            invitation = await main.invite_parent(ids.group_id, payload, user, database)

        self.assertEqual(invitation["token"], "fixed-token")
        self.assertEqual(len(database.notifications), 1)
        self.assertIn("invited you to join", database.notifications[0]["body"])
        self.assertEqual(database.notifications[0]["action_url"], "/?invite=fixed-token")

    async def test_group_access_request_blocks_existing_group_member(self):
        ids = ActionIds()
        database = AccessRequestDatabase(ids, active_group_id=uuid4())
        user = {"id": str(ids.parent_id), "name": "Requester", "email": "requester@example.com"}
        payload = main.JoinRequestIn(group_id=ids.group_id)

        with self.assertRaises(HTTPException) as raised:
            await main.request_group_access(payload, user, database)

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.detail, "Leave your current group before joining another group")

    async def test_group_access_request_notifies_creator_and_approval_adds_member(self):
        ids = ActionIds()
        database = AccessRequestDatabase(ids)
        requester = {"id": str(ids.parent_id), "name": "Requester", "email": "requester@example.com"}
        creator = {"id": str(ids.organiser_id), "name": "Organiser", "email": "organiser@example.com"}

        request = await main.request_group_access(main.JoinRequestIn(group_id=ids.group_id), requester, database)
        decision = await main.decide_group_access_request(
            ids.group_id,
            request["id"],
            main.JoinRequestDecisionIn(status="approved"),
            creator,
            database,
        )

        self.assertEqual(decision["status"], "approved")
        self.assertIn((ids.group_id, ids.parent_id), database.memberships)
        self.assertEqual([row["kind"] for row in database.notifications], ["group_access_request", "group_access_decision"])

    async def test_trip_cannot_start_before_service_date(self):
        user_id = uuid4()
        group_id = uuid4()
        future_trip = self.trip(group_id, user_id, date.today() + timedelta(days=1), "planned")
        database = AsyncMock()

        def fetchrow(query, *args):
            if "SELECT * FROM trips WHERE id" in query:
                return future_trip
            if "FROM group_members" in query:
                return Record({"group_id": group_id, "user_id": user_id, "role": "member", "status": "active"})
            raise AssertionError(query)

        database.fetchrow.side_effect = fetchrow
        user = {"id": str(user_id)}

        with self.assertRaises(HTTPException) as raised:
            await main.start_trip(future_trip["id"], user, database)

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.detail, "Trip can only be started on its service date")

    async def test_trip_cannot_start_until_driver_accepts(self):
        user_id = uuid4()
        group_id = uuid4()
        today_trip = self.trip(group_id, user_id, date.today(), "planned")
        database = AsyncMock()

        def fetchrow(query, *args):
            if "SELECT * FROM trips WHERE id" in query:
                return today_trip
            if "FROM group_members" in query:
                return Record({"group_id": group_id, "user_id": user_id, "role": "member", "status": "active"})
            raise AssertionError(query)

        def fetchval(query, *args):
            if "FROM trip_responses" in query:
                return None
            raise AssertionError(query)

        database.fetchrow.side_effect = fetchrow
        database.fetchval.side_effect = fetchval
        user = {"id": str(user_id)}

        with self.assertRaises(HTTPException) as raised:
            await main.start_trip(today_trip["id"], user, database)

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.detail, "Accept this trip before starting it")

    async def test_pending_assignment_acceptance_is_recorded_once(self):
        ids = ActionIds()
        database = TripActionDatabase(ids)
        user = {"id": str(ids.driver_id), "name": "Driver"}

        row = await main.respond_trip(ids.trip_id, main.TripResponseIn(status="accepted"), user, database)

        self.assertEqual(row["status"], "accepted")
        self.assertEqual(database.response_status, "accepted")
        self.assertIn(("trip.response", ids.trip_id), database.audit_actions)

    async def test_api_prevents_accepted_assignment_from_decline(self):
        ids = ActionIds()
        database = TripActionDatabase(ids)
        database.response_status = "accepted"
        user = {"id": str(ids.driver_id), "name": "Driver"}

        with self.assertRaises(HTTPException) as raised:
            await main.respond_trip(ids.trip_id, main.TripResponseIn(status="declined"), user, database)

        self.assertEqual(raised.exception.status_code, 409)

    async def test_api_prevents_declined_assignment_from_accept(self):
        ids = ActionIds()
        database = TripActionDatabase(ids)
        database.response_status = "declined"
        user = {"id": str(ids.driver_id), "name": "Driver"}

        with self.assertRaises(HTTPException) as raised:
            await main.respond_trip(ids.trip_id, main.TripResponseIn(status="accepted"), user, database)

        self.assertEqual(raised.exception.status_code, 409)

    async def test_duplicate_acceptance_is_rejected(self):
        ids = ActionIds()
        database = TripActionDatabase(ids)
        user = {"id": str(ids.driver_id), "name": "Driver"}

        await main.respond_trip(ids.trip_id, main.TripResponseIn(status="accepted"), user, database)
        with self.assertRaises(HTTPException) as raised:
            await main.respond_trip(ids.trip_id, main.TripResponseIn(status="accepted"), user, database)

        self.assertEqual(raised.exception.status_code, 409)

    async def test_duplicate_decline_is_rejected(self):
        ids = ActionIds()
        database = TripActionDatabase(ids)
        user = {"id": str(ids.driver_id), "name": "Driver"}

        await main.respond_trip(ids.trip_id, main.TripResponseIn(status="declined"), user, database)
        with self.assertRaises(HTTPException) as raised:
            await main.respond_trip(ids.trip_id, main.TripResponseIn(status="declined"), user, database)

        self.assertEqual(raised.exception.status_code, 409)

    async def test_idempotent_activity_notification_never_sends_email(self):
        user_id = uuid4()
        entity_id = uuid4()
        database = NotificationDatabase(user_id)

        with patch.object(main, "deliver_push", new=AsyncMock()) as push, patch.object(main, "send_email", new=AsyncMock()) as email:
            await main.notify(database, user_id, "handover", "Title", "Body", "trip", entity_id, email=True, idempotency_key="handover:key")
            await main.notify(database, user_id, "handover", "Title", "Body", "trip", entity_id, email=True, idempotency_key="handover:key")

        self.assertEqual(database.inserted_notifications, 1)
        push.assert_awaited_once()
        email.assert_not_awaited()

    async def test_password_reset_is_generic_single_use_and_replaces_password(self):
        database = PasswordResetDatabase()
        main.settings.environment = "production"
        main.settings.expose_development_verification_codes = False

        with patch.object(main.secrets, "token_urlsafe", return_value="reset-token-12345678901234567890"):
            with patch.object(main, "send_email", new=AsyncMock(return_value="sent")) as email:
                existing = await main.password_reset_start(main.PasswordResetStartIn(email="Parent@Example.com"), database)
                missing = await main.password_reset_start(main.PasswordResetStartIn(email="missing@example.com"), database)

        self.assertEqual(existing["message"], missing["message"])
        self.assertNotIn("reset_token", existing)
        email.assert_awaited_once()

        await main.password_reset_complete(
            main.PasswordResetCompleteIn(token="reset-token-12345678901234567890", password="new-password-123"),
            database,
        )

        self.assertFalse(main.passwords.verify("old-password-123", database.password_hash))
        self.assertTrue(main.passwords.verify("new-password-123", database.password_hash))
        with self.assertRaises(HTTPException) as reused:
            await main.password_reset_complete(
                main.PasswordResetCompleteIn(token="reset-token-12345678901234567890", password="another-password-123"),
                database,
            )
        self.assertEqual(reused.exception.status_code, 400)

    async def test_expired_password_reset_token_is_rejected(self):
        database = PasswordResetDatabase(token="expired-token-12345678901234567890", token_valid=False)

        with self.assertRaises(HTTPException) as raised:
            await main.password_reset_complete(
                main.PasswordResetCompleteIn(token="expired-token-12345678901234567890", password="new-password-123"),
                database,
            )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("invalid or expired", raised.exception.detail)

    async def test_swap_accept_reassigns_driver_and_second_or_unauthorised_decision_fails(self):
        ids = ActionIds()
        database = SwapDecisionDatabase(ids)
        proposed = {"id": str(ids.proposed_driver_id), "name": "Driver B"}

        row = await main.decide_roster_change(database.request_id, main.ChangeRequestDecisionIn(status="accepted"), proposed, database)

        self.assertEqual(row["status"], "accepted")
        self.assertEqual(database.trip_driver_id, ids.proposed_driver_id)
        self.assertIn((ids.trip_id, ids.proposed_driver_id, "accepted"), database.trip_responses)
        with self.assertRaises(HTTPException) as second:
            await main.decide_roster_change(database.request_id, main.ChangeRequestDecisionIn(status="accepted"), proposed, database)
        self.assertEqual(second.exception.status_code, 409)

        other_database = SwapDecisionDatabase(ids)
        with self.assertRaises(HTTPException) as unauthorised:
            await main.decide_roster_change(other_database.request_id, main.ChangeRequestDecisionIn(status="accepted"), {"id": str(ids.unrelated_id)}, other_database)
        self.assertEqual(unauthorised.exception.status_code, 403)

    async def test_swap_decline_keeps_original_driver(self):
        ids = ActionIds()
        database = SwapDecisionDatabase(ids)
        proposed = {"id": str(ids.proposed_driver_id), "name": "Driver B"}

        row = await main.decide_roster_change(database.request_id, main.ChangeRequestDecisionIn(status="declined"), proposed, database)

        self.assertEqual(row["status"], "declined")
        self.assertEqual(database.trip_driver_id, ids.driver_id)
        self.assertEqual(database.trip_responses, [])

    async def test_accepted_swap_cannot_be_declined_later(self):
        ids = ActionIds()
        database = SwapDecisionDatabase(ids)
        database.status = "accepted"
        proposed = {"id": str(ids.proposed_driver_id), "name": "Driver B"}

        with self.assertRaises(HTTPException) as raised:
            await main.decide_roster_change(database.request_id, main.ChangeRequestDecisionIn(status="declined"), proposed, database)

        self.assertEqual(raised.exception.status_code, 409)

    async def test_declined_swap_cannot_be_accepted_later(self):
        ids = ActionIds()
        database = SwapDecisionDatabase(ids)
        database.status = "declined"
        proposed = {"id": str(ids.proposed_driver_id), "name": "Driver B"}

        with self.assertRaises(HTTPException) as raised:
            await main.decide_roster_change(database.request_id, main.ChangeRequestDecisionIn(status="accepted"), proposed, database)

        self.assertEqual(raised.exception.status_code, 409)

    async def test_old_swap_notification_deep_link_can_view_but_not_decide_final_request(self):
        ids = ActionIds()
        database = TripActionDatabase(ids)
        database.open_swap_exists = True

        trip = await main.trip_with_permission(database, ids.proposed_driver_id, ids.trip_id)

        self.assertEqual(trip["id"], ids.trip_id)

        decision_database = SwapDecisionDatabase(ids)
        decision_database.status = "declined"
        proposed = {"id": str(ids.proposed_driver_id), "name": "Driver B"}
        with self.assertRaises(HTTPException) as raised:
            await main.decide_roster_change(
                decision_database.request_id,
                main.ChangeRequestDecisionIn(status="accepted"),
                proposed,
                decision_database,
            )

        self.assertEqual(raised.exception.status_code, 409)

    async def test_duplicate_open_swap_request_is_rejected(self):
        ids = ActionIds()
        database = TripActionDatabase(ids)
        database.open_swap_exists = True
        user = {"id": str(ids.driver_id), "name": "Driver"}
        payload = main.ChangeRequestIn(
            request_type="swap",
            proposed_driver_user_id=ids.proposed_driver_id,
            note="Assigned driver requested a trip swap.",
        )

        with self.assertRaises(HTTPException) as raised:
            await main.request_roster_change(ids.trip_id, payload, user, database)

        self.assertEqual(raised.exception.status_code, 409)

    async def test_delete_roster_soft_deletes_only_unaccepted_planned_rosters(self):
        ids = ActionIds()
        database = DeleteRosterDatabase(ids)
        user = {"id": str(ids.organiser_id)}

        result = await main.delete_roster(ids.trip_id, user, database)

        self.assertEqual(result["status"], "deleted")
        self.assertEqual(database.trip["status"], "ignored")
        self.assertTrue(database.cancelled_open_requests)
        self.assertTrue(database.notifications_marked_read)
        self.assertIn(("trip.deleted", ids.trip_id), database.audit_actions)

        accepted_database = DeleteRosterDatabase(ids, accepted=True)
        with self.assertRaises(HTTPException) as accepted:
            await main.delete_roster(ids.trip_id, user, accepted_database)
        self.assertEqual(accepted.exception.status_code, 409)

        started_database = DeleteRosterDatabase(ids, status="started")
        with self.assertRaises(HTTPException) as started:
            await main.delete_roster(ids.trip_id, user, started_database)
        self.assertEqual(started.exception.status_code, 409)

    async def test_delete_notification_removes_only_current_users_inbox_row(self):
        user_id = uuid4()
        notification_id = uuid4()
        database = AsyncMock()

        result = await main.delete_notification(notification_id, {"id": str(user_id)}, database)

        self.assertEqual(result["status"], "deleted")
        database.execute.assert_awaited_once_with(
            "DELETE FROM notifications WHERE id = $1 AND user_id = $2",
            notification_id,
            user_id,
        )

    async def test_clear_read_notifications_only_removes_current_users_read_inbox_rows(self):
        user_id = uuid4()
        database = AsyncMock()

        result = await main.delete_read_notifications({"id": str(user_id)}, database)

        self.assertEqual(result["status"], "deleted")
        database.execute.assert_awaited_once_with(
            "DELETE FROM notifications WHERE user_id = $1 AND read_at IS NOT NULL",
            user_id,
        )

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
            key = args[6]
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


class ActionIds:
    def __init__(self):
        self.group_id = uuid4()
        self.trip_id = uuid4()
        self.child_id = uuid4()
        self.driver_id = uuid4()
        self.parent_id = uuid4()
        self.organiser_id = uuid4()
        self.proposed_driver_id = uuid4()
        self.unrelated_id = uuid4()


class AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_):
        return None


class RouteDatabase:
    def __init__(
        self,
        ids: ActionIds,
        *,
        status="started",
        trip_type="pickup",
        ended_at=None,
        parent_allowed=True,
        latest_location=True,
        latest_location_received_at=None,
    ):
        self.ids = ids
        self.parent_allowed = parent_allowed
        self.trip = Record(
            {
                "id": ids.trip_id,
                "group_id": ids.group_id,
                "driver_user_id": ids.driver_id,
                "service_date": date.today(),
                "trip_type": trip_type,
                "expected_time": time(8, 0),
                "status": status,
                "delay_minutes": 0,
                "delay_note": None,
                "cancellation_reason": None,
                "started_at": datetime.now(UTC) if status == "started" else None,
                "ended_at": ended_at,
                "safety_timeout_at": None,
                "created_by": ids.organiser_id,
                "created_at": None,
            }
        )
        if latest_location is None:
            self.latest_location = None
        else:
            self.latest_location = Record(
                {
                    "id": uuid4(),
                    "trip_id": ids.trip_id,
                    "driver_user_id": ids.driver_id,
                    "latitude": 52.52,
                    "longitude": 13.405,
                    "accuracy_meters": 12,
                    "speed_mps": None,
                    "heading_degrees": None,
                    "recorded_at": datetime.now(UTC),
                    "received_at": latest_location_received_at or datetime.now(UTC),
                }
            )

    async def fetchrow(self, query, *args):
        if "SELECT * FROM trips WHERE id" in query:
            return self.trip
        if "FROM group_members" in query:
            return Record({"group_id": self.ids.group_id, "user_id": args[1], "role": "member", "status": "active"})
        if "FROM trip_locations" in query:
            return self.latest_location
        if "FROM carpool_groups cg" in query and "JOIN schools" in query:
            return Record({"name": "School", "address": "1 School St", "city": "Berlin", "country": "DE"})
        raise AssertionError(query)

    async def fetchval(self, query, *args):
        if "SELECT created_by FROM carpool_groups" in query:
            return self.ids.organiser_id
        if "FROM trip_children tc" in query and "c.parent_id" in query:
            return 1 if self.parent_allowed and args[1] == self.ids.parent_id else None
        if "FROM roster_change_requests" in query:
            return None
        raise AssertionError(query)

    async def fetch(self, query, *args):
        if "FROM trip_children tc" in query and "JOIN children" in query:
            if len(args) > 1 and args[1] != self.ids.parent_id:
                return []
            return [
                Record(
                    {
                        "id": self.ids.child_id,
                        "name": "Child",
                        "home_address": "1 Home St",
                        "home_city": "Berlin",
                        "home_country": "DE",
                        "home_latitude": None,
                        "home_longitude": None,
                        "pickup_status": "pending",
                        "dropoff_status": "pending",
                    }
                )
            ]
        raise AssertionError(query)


class PendingActionsDatabase:
    def __init__(
        self,
        ids: ActionIds,
        *,
        assignment_response_status=None,
        swap_status=None,
        swap_proposed_user_id=None,
        invitation=False,
    ):
        self.ids = ids
        self.assignment_response_status = assignment_response_status
        self.swap_status = swap_status
        self.swap_proposed_user_id = swap_proposed_user_id
        self.invitation = invitation
        self.swap_request_id = uuid4()
        self.invitation_id = uuid4()

    async def fetch(self, query, *args):
        if "FROM invitations" in query:
            if not self.invitation:
                return []
            return [
                Record(
                    {
                        "id": self.invitation_id,
                        "token": "invite-token",
                        "email": args[0],
                        "expires_at": None,
                        "created_at": None,
                    }
                )
            ]
        if "LEFT JOIN trip_responses" in query:
            if args[0] != self.ids.driver_id or self.assignment_response_status:
                return []
            return [
                Record(
                    {
                        "id": self.ids.trip_id,
                        "group_id": self.ids.group_id,
                        "driver_user_id": self.ids.driver_id,
                        "service_date": date.today(),
                        "trip_type": "pickup",
                        "expected_time": time(8, 0),
                        "status": "planned",
                        "delay_minutes": 0,
                        "delay_note": None,
                        "cancellation_reason": None,
                        "started_at": None,
                        "ended_at": None,
                        "safety_timeout_at": None,
                        "created_by": self.ids.organiser_id,
                        "created_at": None,
                        "driver_name": "Driver A",
                    }
                )
            ]
        if "FROM roster_change_requests rcr" in query:
            if self.swap_status != "open" or self.swap_proposed_user_id != args[0]:
                return []
            return [
                Record(
                    {
                        "id": self.swap_request_id,
                        "trip_id": self.ids.trip_id,
                        "requested_by": self.ids.driver_id,
                        "request_type": "swap",
                        "proposed_driver_user_id": self.swap_proposed_user_id,
                        "note": "Please cover this trip",
                        "status": "open",
                        "created_at": None,
                        "service_date": date.today(),
                        "trip_type": "pickup",
                        "expected_time": time(8, 0),
                        "requester_name": "Driver A",
                    }
                )
            ]
        raise AssertionError(query)


class PasswordResetDatabase:
    def __init__(self, token=None, token_valid=True):
        self.user_id = uuid4()
        self.email = "parent@example.com"
        self.password_hash = main.passwords.hash("old-password-123")
        self.tokens = []
        self.audit_actions = []
        if token:
            self.tokens.append(
                Record(
                    {
                        "id": uuid4(),
                        "user_id": self.user_id,
                        "token_hash": main.reset_token_hash(token),
                        "used_at": None,
                        "valid": token_valid,
                    }
                )
            )

    def acquire(self):
        return AsyncContext(self)

    def transaction(self):
        return AsyncContext(self)

    async def fetchrow(self, query, *args):
        if "SELECT id, email FROM users WHERE email" in query:
            if args[0] == self.email:
                return Record({"id": self.user_id, "email": self.email})
            return None
        if "FROM password_reset_tokens prt" in query:
            token_hash = args[0]
            for token in self.tokens:
                if token["token_hash"] == token_hash and token["used_at"] is None and token.get("valid", True):
                    return Record({**token, "account_user_id": self.user_id})
            return None
        raise AssertionError(query)

    async def execute(self, query, *args):
        if "UPDATE password_reset_tokens SET used_at = now() WHERE user_id" in query:
            for token in self.tokens:
                if token["user_id"] == args[0] and token["used_at"] is None:
                    token["used_at"] = "now"
            return None
        if "INSERT INTO password_reset_tokens" in query:
            self.tokens.append(
                Record(
                    {
                        "id": uuid4(),
                        "user_id": args[0],
                        "token_hash": args[1],
                        "used_at": None,
                        "valid": True,
                    }
                )
            )
            return None
        if "UPDATE users SET password_hash" in query:
            self.password_hash = args[1]
            return None
        if "UPDATE password_reset_tokens SET used_at = now() WHERE id" in query:
            for token in self.tokens:
                if token["id"] == args[0]:
                    token["used_at"] = "now"
            return None
        if "INSERT INTO audit_records" in query:
            self.audit_actions.append((args[0], "user.password_reset"))
            return None
        raise AssertionError(query)


class TripActionDatabase:
    def __init__(self, ids: ActionIds):
        self.ids = ids
        self.notifications = []
        self.seen_notification_keys = set()
        self.audit_actions = []
        self.open_swap_exists = False
        self.response_status = None
        self.trip = Record(
            {
                "id": ids.trip_id,
                "group_id": ids.group_id,
                "driver_user_id": ids.driver_id,
                "service_date": date.today(),
                "trip_type": "pickup",
                "expected_time": time(8, 0),
                "status": "planned",
                "delay_minutes": 0,
                "delay_note": None,
                "cancellation_reason": None,
                "started_at": None,
                "ended_at": None,
                "safety_timeout_at": None,
                "created_by": ids.organiser_id,
                "created_at": None,
            }
        )

    def acquire(self):
        return AsyncContext(self)

    def transaction(self):
        return AsyncContext(self)

    async def fetchrow(self, query, *args):
        if "SELECT * FROM trips WHERE id" in query:
            return self.trip
        if "FROM group_members" in query:
            return Record({"group_id": self.ids.group_id, "user_id": args[1], "role": "member", "status": "active"})
        if "UPDATE trips SET status = 'delayed'" in query:
            self.trip = Record({**self.trip, "status": "delayed", "delay_minutes": args[1], "delay_note": args[2]})
            return self.trip
        if "INSERT INTO notifications" in query:
            key = args[6]
            if key in self.seen_notification_keys:
                return None
            self.seen_notification_keys.add(key)
            self.notifications.append(
                {
                    "user_id": args[0],
                    "kind": args[1],
                    "title": args[2],
                    "body": args[3],
                    "idempotency_key": key,
                    "action_url": args[7],
                }
            )
            return Record({"id": uuid4()})
        if "SELECT email FROM users" in query:
            return Record({"email": "recipient@example.com"})
        if "INSERT INTO roster_change_requests" in query:
            return Record(
                {
                    "id": uuid4(),
                    "trip_id": args[0],
                    "requested_by": args[1],
                    "request_type": args[2],
                    "proposed_driver_user_id": args[3],
                    "note": args[4],
                    "status": "open",
                    "created_at": None,
                }
            )
        if "INSERT INTO trip_responses" in query:
            if self.response_status is not None:
                return None
            self.response_status = args[2]
            return Record({"trip_id": args[0], "user_id": args[1], "status": args[2], "note": args[3], "created_at": None})
        if "FROM users WHERE id" in query:
            return Record({"id": self.trip["driver_user_id"], "name": "Driver", "email": "driver@example.com", "phone": None})
        if "FROM trip_responses" in query or "FROM trip_locations" in query:
            return None
        if "INSERT INTO handovers" in query:
            return Record(
                {
                    "id": uuid4(),
                    "trip_id": args[0],
                    "child_id": args[1],
                    "handover_type": args[2],
                    "completed_by": args[3],
                    "note": args[4],
                    "completed_at": None,
                }
            )
        if "SELECT parent_id, name FROM children" in query:
            return Record({"parent_id": self.ids.parent_id, "name": "Child"})
        raise AssertionError(query)

    async def fetchval(self, query, *args):
        if "SELECT created_by FROM carpool_groups" in query:
            return self.ids.organiser_id
        if "LEFT JOIN driver_approvals" in query:
            return None
        if "FROM roster_change_requests" in query:
            return 1 if self.open_swap_exists else None
        if "FROM trip_children tc" in query and "JOIN children" in query:
            return None
        if "SELECT 1 FROM trip_children WHERE trip_id" in query:
            return 1
        raise AssertionError(query)

    async def fetch(self, query, *args):
        if "mobile_devices" in query:
            return []
        if "SELECT DISTINCT c.parent_id, c.name AS child_name" in query:
            return [
                Record({"parent_id": self.ids.parent_id, "child_name": "Child"}),
            ]
        if "FROM trip_children" in query and "JOIN children" in query:
            return [
                Record(
                    {
                        "id": uuid4(),
                        "trip_id": self.ids.trip_id,
                        "child_id": self.ids.child_id,
                        "pickup_status": "pending",
                        "dropoff_status": "pending",
                        "name": "Child",
                        "year_group": "1",
                        "pickup_notes": None,
                        "emergency_contact_name": "Emergency",
                        "emergency_contact_phone": "123",
                        "parent_id": self.ids.parent_id,
                        "home_address": None,
                        "home_city": None,
                        "home_country": None,
                        "home_latitude": None,
                        "home_longitude": None,
                        "home_place_id": None,
                    }
                )
            ]
        raise AssertionError(query)

    async def execute(self, query, *args):
        if "INSERT INTO audit_records" in query:
            self.audit_actions.append((args[3], args[2]))
            return None
        if "UPDATE trip_children SET" in query:
            return None
        raise AssertionError(query)


class SwapDecisionDatabase:
    def __init__(self, ids: ActionIds):
        self.ids = ids
        self.request_id = uuid4()
        self.status = "open"
        self.trip_driver_id = ids.driver_id
        self.trip_responses = []
        self.notifications = []
        self.audit_actions = []

    def acquire(self):
        return AsyncContext(self)

    def transaction(self):
        return AsyncContext(self)

    async def fetchrow(self, query, *args):
        if "FROM roster_change_requests rcr" in query and "JOIN trips" in query:
            return Record(
                {
                    "id": self.request_id,
                    "trip_id": self.ids.trip_id,
                    "requested_by": self.ids.driver_id,
                    "request_type": "swap",
                    "proposed_driver_user_id": self.ids.proposed_driver_id,
                    "note": "Please cover this trip",
                    "status": self.status,
                    "group_id": self.ids.group_id,
                    "driver_user_id": self.trip_driver_id,
                    "service_date": date.today(),
                    "trip_type": "pickup",
                    "expected_time": time(8, 0),
                    "created_by": self.ids.organiser_id,
                }
            )
        if "FROM group_members" in query:
            return Record({"group_id": self.ids.group_id, "user_id": args[1], "role": "member", "status": "active"})
        if "UPDATE roster_change_requests" in query:
            if self.status != "open":
                return None
            self.status = args[1]
            return Record(
                {
                    "id": args[0],
                    "trip_id": self.ids.trip_id,
                    "requested_by": self.ids.driver_id,
                    "request_type": "swap",
                    "proposed_driver_user_id": self.ids.proposed_driver_id,
                    "note": None,
                    "status": self.status,
                }
            )
        if "INSERT INTO notifications" in query:
            self.notifications.append({"user_id": args[0], "kind": args[1], "action_url": args[7]})
            return Record({"id": uuid4()})
        raise AssertionError(query)

    async def fetch(self, query, *args):
        if "mobile_devices" in query:
            return []
        raise AssertionError(query)

    async def execute(self, query, *args):
        if "UPDATE trips SET driver_user_id" in query:
            self.trip_driver_id = args[1]
            return None
        if "INSERT INTO trip_responses" in query:
            self.trip_responses.append((args[0], args[1], "accepted"))
            return None
        if "UPDATE roster_change_requests" in query and "id <>" in query:
            return None
        if "INSERT INTO audit_records" in query:
            self.audit_actions.append(("trip.deleted", args[2]))
            return None
        raise AssertionError(query)


class DeleteRosterDatabase:
    def __init__(self, ids: ActionIds, *, status="planned", accepted=False):
        self.ids = ids
        self.accepted = accepted
        self.cancelled_open_requests = False
        self.notifications_marked_read = False
        self.audit_actions = []
        self.trip = Record(
            {
                "id": ids.trip_id,
                "group_id": ids.group_id,
                "driver_user_id": ids.driver_id,
                "service_date": date.today(),
                "trip_type": "pickup",
                "expected_time": time(8, 0),
                "status": status,
                "delay_minutes": 0,
                "delay_note": None,
                "cancellation_reason": None,
                "started_at": None,
                "ended_at": None,
                "safety_timeout_at": None,
                "created_by": ids.organiser_id,
                "created_at": None,
            }
        )

    def acquire(self):
        return AsyncContext(self)

    def transaction(self):
        return AsyncContext(self)

    async def fetchrow(self, query, *args):
        if "SELECT * FROM trips WHERE id" in query:
            return self.trip
        if "FROM group_members" in query:
            return Record({"group_id": self.ids.group_id, "user_id": args[1], "role": "creator", "status": "active"})
        if "UPDATE trips" in query and "SET status = 'ignored'" in query:
            if self.trip["status"] != "planned":
                return None
            self.trip = Record({**self.trip, "status": "ignored", "cancellation_reason": "Roster deleted before driver acceptance"})
            return self.trip
        raise AssertionError(query)

    async def fetchval(self, query, *args):
        if "SELECT created_by FROM carpool_groups" in query:
            return self.ids.organiser_id
        if "FROM trip_children" in query:
            return None
        if "FROM roster_change_requests" in query:
            return None
        if "FROM trip_responses" in query:
            return 1 if self.accepted else None
        raise AssertionError(query)

    async def execute(self, query, *args):
        if "UPDATE roster_change_requests SET status = 'cancelled'" in query:
            self.cancelled_open_requests = True
            return None
        if "UPDATE notifications" in query:
            self.notifications_marked_read = True
            return None
        if "INSERT INTO audit_records" in query:
            self.audit_actions.append(("trip.deleted", args[2]))
            return None
        raise AssertionError(query)


class InvitationDatabase:
    def __init__(self, ids: ActionIds):
        self.ids = ids
        self.notifications = []
        self.audit_actions = []

    async def fetchrow(self, query, *args):
        if "FROM group_members" in query:
            return Record({"group_id": self.ids.group_id, "user_id": args[1], "role": "creator", "status": "active"})
        if "INSERT INTO invitations" in query:
            return Record(
                {
                    "id": uuid4(),
                    "group_id": args[0],
                    "email": args[1],
                    "token": args[2],
                    "status": "pending",
                    "expires_at": None,
                    "created_at": None,
                }
            )
        if "SELECT id FROM users" in query:
            return Record({"id": self.ids.parent_id})
        if "INSERT INTO notifications" in query:
            self.notifications.append({"user_id": args[0], "kind": args[1], "title": args[2], "body": args[3], "action_url": args[7]})
            return Record({"id": uuid4()})
        if "SELECT email FROM users" in query:
            return Record({"email": "recipient@example.com"})
        raise AssertionError(query)

    async def fetch(self, query, *args):
        if "mobile_devices" in query:
            return []
        raise AssertionError(query)

    async def execute(self, query, *args):
        if "INSERT INTO audit_records" in query:
            self.audit_actions.append((args[3], args[1]))
            return None
        raise AssertionError(query)


class AccessRequestDatabase:
    def __init__(self, ids: ActionIds, active_group_id=None):
        self.ids = ids
        self.active_group_id = active_group_id
        self.request_id = uuid4()
        self.request_status = "pending"
        self.notifications = []
        self.memberships = set()

    def acquire(self):
        return AsyncContext(self)

    def transaction(self):
        return AsyncContext(self)

    async def fetchval(self, query, *args):
        if "SELECT group_id FROM group_members" in query:
            return self.active_group_id
        raise AssertionError(query)

    async def fetchrow(self, query, *args):
        if "FROM carpool_groups cg" in query and "school_name" in query:
            return Record({"id": self.ids.group_id, "name": "Group", "created_by": self.ids.organiser_id, "school_name": "School", "school_city": "City"})
        if "INSERT INTO group_join_requests" in query:
            self.request_status = "pending"
            return Record(
                {
                    "id": self.request_id,
                    "group_id": args[0],
                    "requester_user_id": args[1],
                    "requester_note": args[2],
                    "status": self.request_status,
                    "decided_by": None,
                    "decided_at": None,
                    "created_at": None,
                    "updated_at": None,
                }
            )
        if "INSERT INTO notifications" in query:
            self.notifications.append({"user_id": args[0], "kind": args[1], "title": args[2], "body": args[3]})
            return Record({"id": uuid4()})
        if "SELECT email FROM users" in query:
            return Record({"email": "recipient@example.com"})
        if "FROM group_members" in query:
            return Record({"group_id": args[0], "user_id": args[1], "role": "creator", "status": "active"})
        if "FROM group_join_requests gjr" in query and "JOIN users" in query:
            return Record(
                {
                    "id": self.request_id,
                    "group_id": self.ids.group_id,
                    "requester_user_id": self.ids.parent_id,
                    "requester_name": "Requester",
                    "requester_email": "requester@example.com",
                    "requester_note": None,
                    "status": self.request_status,
                    "created_at": None,
                }
            )
        if "UPDATE group_join_requests" in query:
            self.request_status = args[2]
            return Record(
                {
                    "id": args[0],
                    "group_id": args[1],
                    "requester_user_id": self.ids.parent_id,
                    "requester_note": None,
                    "status": self.request_status,
                    "decided_by": args[3],
                    "decided_at": None,
                    "created_at": None,
                    "updated_at": None,
                }
            )
        raise AssertionError(query)

    async def fetch(self, query, *args):
        if "mobile_devices" in query:
            return []
        raise AssertionError(query)

    async def execute(self, query, *args):
        if "INSERT INTO audit_records" in query:
            return None
        if "INSERT INTO group_members" in query:
            self.memberships.add((args[0], args[1]))
            return None
        raise AssertionError(query)


class TripListDatabase:
    def __init__(self, user_id, group_id, trips, driver):
        self.user_id = user_id
        self.group_id = group_id
        self.trips = trips
        self.driver = driver
        self.fetch_queries = []

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
        if "FROM roster_change_requests" in query:
            return None
        raise AssertionError(query)

    async def fetch(self, query, *args):
        self.fetch_queries.append(query)
        if "FROM users WHERE id = ANY" in query:
            return [self.driver]
        if "FROM carpool_groups WHERE id = ANY" in query:
            return [Record({"id": self.group_id, "created_by": self.user_id})]
        if "FROM trip_children" in query and "JOIN children" in query:
            return []
        if "FROM trip_responses" in query:
            return []
        if "FROM trip_locations" in query:
            return []
        if "FROM trips" in query and "CURRENT_DATE" in query:
            rows = [trip for trip in self.trips if trip["service_date"] < date.today() or trip["status"] in {"completed", "cancelled", "driver_unavailable", "ignored"}]
            return sorted(rows, key=lambda trip: (trip["service_date"], trip["expected_time"]), reverse=True)
        if "FROM trips" in query:
            from_date = args[1]
            to_date = args[2]
            rows = [
                trip
                for trip in self.trips
                if trip["service_date"] >= from_date
                and (to_date is None or trip["service_date"] <= to_date)
                and trip["status"] not in {"completed", "cancelled", "driver_unavailable", "ignored"}
            ]
            return sorted(rows, key=lambda trip: (trip["service_date"], trip["expected_time"]))
        if "FROM roster_change_requests" in query:
            return []
        raise AssertionError(query)


if __name__ == "__main__":
    unittest.main()
