ALTER TABLE children
    ADD COLUMN IF NOT EXISTS home_address text,
    ADD COLUMN IF NOT EXISTS home_city text,
    ADD COLUMN IF NOT EXISTS home_country text NOT NULL DEFAULT 'DE',
    ADD COLUMN IF NOT EXISTS home_latitude numeric(9,6),
    ADD COLUMN IF NOT EXISTS home_longitude numeric(9,6),
    ADD COLUMN IF NOT EXISTS home_place_id text;
