ALTER TABLE carpool_groups
    ADD COLUMN IF NOT EXISTS name_normalized text;

UPDATE carpool_groups
SET name_normalized = lower(regexp_replace(btrim(name), '\s+', ' ', 'g'))
WHERE name_normalized IS NULL;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM carpool_groups
        GROUP BY school_id, name_normalized
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'Duplicate normalized group names exist within a school. Review and resolve before applying uniqueness.';
    END IF;
END $$;

ALTER TABLE carpool_groups
    ALTER COLUMN name_normalized SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS carpool_groups_school_normalized_name_idx
    ON carpool_groups (school_id, name_normalized);

ALTER TABLE invitations
    DROP CONSTRAINT IF EXISTS invitations_status_check;

ALTER TABLE invitations
    ADD CONSTRAINT invitations_status_check
    CHECK (status IN ('pending', 'accepted', 'declined', 'revoked'));

ALTER TABLE invitations
    ADD COLUMN IF NOT EXISTS resolved_at timestamptz;

UPDATE invitations
SET resolved_at = COALESCE(accepted_at, created_at)
WHERE status IN ('accepted', 'declined', 'revoked') AND resolved_at IS NULL;

ALTER TABLE children
    ADD COLUMN IF NOT EXISTS home_postcode text;

ALTER TABLE children
    DROP CONSTRAINT IF EXISTS children_home_postcode_check;

ALTER TABLE children
    ADD CONSTRAINT children_home_postcode_check
    CHECK (home_postcode IS NULL OR home_postcode ~ '^\d{4}$');

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM trips
        WHERE status IN ('planned', 'started', 'delayed')
        GROUP BY group_id, service_date, trip_type, expected_time
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'Duplicate active/future roster timestamps exist. Review and resolve before applying roster uniqueness.';
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS trips_group_schedule_type_active_idx
    ON trips (group_id, service_date, trip_type, expected_time)
    WHERE status IN ('planned', 'started', 'delayed');
