ALTER TABLE users
    ADD COLUMN IF NOT EXISTS email_verified_at timestamptz;

UPDATE users
SET email_verified_at = COALESCE(email_verified_at, created_at)
WHERE email_verified_at IS NULL;

CREATE TABLE IF NOT EXISTS email_verifications (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email text NOT NULL,
    token text NOT NULL UNIQUE,
    code_hash text NOT NULL,
    purpose text NOT NULL,
    name text,
    phone text,
    attempt_count integer NOT NULL DEFAULT 0,
    expires_at timestamptz NOT NULL,
    last_sent_at timestamptz,
    used_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS email_verifications_email_purpose_idx
    ON email_verifications (email, purpose, created_at DESC);

ALTER TABLE children
    ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'removed'));

WITH ranked_memberships AS (
    SELECT id,
           row_number() OVER (PARTITION BY user_id ORDER BY joined_at DESC, id DESC) AS active_rank
    FROM group_members
    WHERE status = 'active'
)
UPDATE group_members gm
SET status = 'removed'
FROM ranked_memberships ranked
WHERE gm.id = ranked.id
  AND ranked.active_rank > 1;

CREATE UNIQUE INDEX IF NOT EXISTS group_members_one_active_group_per_user_idx
    ON group_members (user_id)
    WHERE status = 'active';

ALTER TABLE invitations
    ADD COLUMN IF NOT EXISTS delivery_status text,
    ADD COLUMN IF NOT EXISTS last_sent_at timestamptz;
