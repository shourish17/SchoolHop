CREATE TABLE IF NOT EXISTS group_join_requests (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id uuid NOT NULL REFERENCES carpool_groups(id) ON DELETE CASCADE,
    requester_user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    requester_note text,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'declined', 'cancelled')),
    decided_by uuid REFERENCES users(id) ON DELETE SET NULL,
    decided_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (group_id, requester_user_id)
);

CREATE INDEX IF NOT EXISTS group_join_requests_group_status_idx
    ON group_join_requests (group_id, status, created_at DESC);
