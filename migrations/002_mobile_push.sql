CREATE TABLE mobile_devices (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider text NOT NULL CHECK (provider IN ('apns', 'fcm')),
    token text NOT NULL,
    platform text,
    enabled boolean NOT NULL DEFAULT true,
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (provider, token)
);

CREATE INDEX mobile_devices_user_enabled_idx ON mobile_devices (user_id, enabled);
