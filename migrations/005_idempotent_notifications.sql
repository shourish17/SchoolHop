ALTER TABLE notifications
    ADD COLUMN IF NOT EXISTS idempotency_key text;

CREATE UNIQUE INDEX IF NOT EXISTS notifications_user_idempotency_key_idx
    ON notifications (user_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

WITH ranked_handovers AS (
    SELECT id,
           row_number() OVER (
               PARTITION BY trip_id, child_id, handover_type
               ORDER BY completed_at, id
           ) AS handover_rank
    FROM handovers
)
DELETE FROM handovers h
USING ranked_handovers ranked
WHERE h.id = ranked.id
  AND ranked.handover_rank > 1;

CREATE UNIQUE INDEX IF NOT EXISTS handovers_trip_child_type_idx
    ON handovers (trip_id, child_id, handover_type);
