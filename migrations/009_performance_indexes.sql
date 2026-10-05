CREATE INDEX IF NOT EXISTS invitations_email_pending_expires_idx
    ON invitations (email, expires_at DESC, created_at DESC)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS trips_group_service_active_idx
    ON trips (group_id, service_date, expected_time)
    WHERE status NOT IN ('completed', 'cancelled', 'driver_unavailable', 'ignored');

CREATE INDEX IF NOT EXISTS trips_group_history_idx
    ON trips (group_id, service_date DESC, expected_time DESC)
    WHERE status IN ('completed', 'cancelled', 'driver_unavailable', 'ignored');

CREATE INDEX IF NOT EXISTS trips_driver_action_idx
    ON trips (driver_user_id, service_date, expected_time)
    WHERE status IN ('planned', 'delayed');

CREATE INDEX IF NOT EXISTS roster_change_requests_proposed_open_swap_idx
    ON roster_change_requests (proposed_driver_user_id, trip_id)
    WHERE request_type = 'swap' AND status = 'open';

CREATE INDEX IF NOT EXISTS roster_change_requests_trip_swap_idx
    ON roster_change_requests (trip_id, proposed_driver_user_id)
    WHERE request_type = 'swap';

CREATE INDEX IF NOT EXISTS notifications_user_created_idx
    ON notifications (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS notifications_user_read_idx
    ON notifications (user_id, read_at)
    WHERE read_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS notifications_entity_unread_idx
    ON notifications (entity_type, entity_id)
    WHERE read_at IS NULL;

CREATE INDEX IF NOT EXISTS trip_locations_received_idx
    ON trip_locations (received_at);
