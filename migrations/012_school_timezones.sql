ALTER TABLE schools
    ADD COLUMN IF NOT EXISTS timezone text NOT NULL DEFAULT 'Australia/Melbourne';

ALTER TABLE schools
    DROP CONSTRAINT IF EXISTS schools_timezone_not_blank;

ALTER TABLE schools
    ADD CONSTRAINT schools_timezone_not_blank CHECK (length(btrim(timezone)) > 0);
