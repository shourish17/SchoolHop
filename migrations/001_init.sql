CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email text NOT NULL UNIQUE,
    password_hash text NOT NULL,
    name text NOT NULL,
    phone text,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE schools (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    address text,
    city text,
    country text NOT NULL DEFAULT 'DE',
    created_by uuid REFERENCES users(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (name, city)
);

CREATE TABLE carpool_groups (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    school_id uuid NOT NULL REFERENCES schools(id) ON DELETE RESTRICT,
    name text NOT NULL,
    created_by uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    invite_only boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE group_members (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id uuid NOT NULL REFERENCES carpool_groups(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role text NOT NULL DEFAULT 'member' CHECK (role IN ('creator', 'member')),
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'removed')),
    joined_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (group_id, user_id)
);

CREATE TABLE invitations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id uuid NOT NULL REFERENCES carpool_groups(id) ON DELETE CASCADE,
    email text NOT NULL,
    token text NOT NULL UNIQUE,
    invited_by uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'revoked')),
    expires_at timestamptz NOT NULL DEFAULT now() + interval '14 days',
    accepted_by uuid REFERENCES users(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    accepted_at timestamptz
);

CREATE TABLE children (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    school_id uuid NOT NULL REFERENCES schools(id) ON DELETE RESTRICT,
    name text NOT NULL,
    year_group text NOT NULL,
    pickup_notes text,
    home_address text,
    home_city text,
    home_country text NOT NULL DEFAULT 'DE',
    home_latitude numeric(9,6),
    home_longitude numeric(9,6),
    home_place_id text,
    emergency_contact_name text NOT NULL,
    emergency_contact_phone text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE driver_approvals (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id uuid NOT NULL REFERENCES carpool_groups(id) ON DELETE CASCADE,
    child_id uuid NOT NULL REFERENCES children(id) ON DELETE CASCADE,
    driver_user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    approved boolean NOT NULL DEFAULT false,
    approved_by uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (group_id, child_id, driver_user_id)
);

CREATE TABLE trips (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id uuid NOT NULL REFERENCES carpool_groups(id) ON DELETE CASCADE,
    driver_user_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    service_date date NOT NULL,
    trip_type text NOT NULL CHECK (trip_type IN ('pickup', 'dropoff')),
    expected_time time NOT NULL,
    status text NOT NULL DEFAULT 'planned' CHECK (status IN ('planned', 'started', 'completed', 'cancelled', 'delayed', 'driver_unavailable')),
    delay_minutes integer NOT NULL DEFAULT 0,
    delay_note text,
    cancellation_reason text,
    started_at timestamptz,
    ended_at timestamptz,
    safety_timeout_at timestamptz,
    created_by uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE trip_children (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    trip_id uuid NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    child_id uuid NOT NULL REFERENCES children(id) ON DELETE RESTRICT,
    pickup_status text NOT NULL DEFAULT 'pending' CHECK (pickup_status IN ('pending', 'picked_up', 'absent')),
    dropoff_status text NOT NULL DEFAULT 'pending' CHECK (dropoff_status IN ('pending', 'dropped_off', 'absent')),
    UNIQUE (trip_id, child_id)
);

CREATE TABLE trip_responses (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    trip_id uuid NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status text NOT NULL CHECK (status IN ('accepted', 'declined')),
    note text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (trip_id, user_id)
);

CREATE TABLE roster_change_requests (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    trip_id uuid NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    requested_by uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    request_type text NOT NULL CHECK (request_type IN ('swap', 'change', 'absence')),
    proposed_driver_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
    note text NOT NULL,
    status text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'accepted', 'declined', 'cancelled')),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE handovers (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    trip_id uuid NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    child_id uuid NOT NULL REFERENCES children(id) ON DELETE RESTRICT,
    handover_type text NOT NULL CHECK (handover_type IN ('picked_up', 'dropped_off', 'absent')),
    completed_by uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    completed_at timestamptz NOT NULL DEFAULT now(),
    note text
);

CREATE TABLE trip_locations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    trip_id uuid NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    driver_user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    latitude numeric(9,6) NOT NULL,
    longitude numeric(9,6) NOT NULL,
    accuracy_meters numeric(8,2),
    speed_mps numeric(8,2),
    heading_degrees numeric(6,2),
    recorded_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX trip_locations_trip_received_idx ON trip_locations (trip_id, received_at DESC);

CREATE TABLE notifications (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind text NOT NULL,
    title text NOT NULL,
    body text NOT NULL,
    entity_type text,
    entity_id uuid,
    read_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE audit_records (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
    group_id uuid REFERENCES carpool_groups(id) ON DELETE CASCADE,
    trip_id uuid REFERENCES trips(id) ON DELETE CASCADE,
    action text NOT NULL,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
