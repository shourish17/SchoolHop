ALTER TABLE group_members
    DROP CONSTRAINT IF EXISTS group_members_role_check;

ALTER TABLE group_members
    ADD CONSTRAINT group_members_role_check
    CHECK (role IN ('creator', 'admin', 'member'));

ALTER TABLE trips
    ADD COLUMN IF NOT EXISTS ended_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS end_reason text;

ALTER TABLE trips
    DROP CONSTRAINT IF EXISTS trips_end_reason_check;

ALTER TABLE trips
    ADD CONSTRAINT trips_end_reason_check
    CHECK (end_reason IS NULL OR end_reason IN ('driver_ended', 'admin_ended'));

CREATE INDEX IF NOT EXISTS trips_active_driver_idx
    ON trips (driver_user_id)
    WHERE status = 'started' AND ended_at IS NULL;

CREATE INDEX IF NOT EXISTS trips_active_child_idx
    ON trip_children (child_id, trip_id);
