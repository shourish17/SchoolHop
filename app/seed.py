from __future__ import annotations

import asyncio
import os
from datetime import date, timedelta

import asyncpg
from passlib.context import CryptContext


DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://schoolhop:schoolhop_dev_password@postgres:5432/schoolhop")
passwords = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


async def main() -> None:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        parent_a = await conn.fetchrow(
            """
            INSERT INTO users (email, password_hash, name, phone)
            VALUES ('ava@example.com', $1, 'Ava Patel', '+49 30 111111')
            ON CONFLICT (email) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            """,
            passwords.hash("schoolhop-dev"),
        )
        parent_b = await conn.fetchrow(
            """
            INSERT INTO users (email, password_hash, name, phone)
            VALUES ('ben@example.com', $1, 'Ben Meyer', '+49 30 222222')
            ON CONFLICT (email) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            """,
            passwords.hash("schoolhop-dev"),
        )
        school = await conn.fetchrow(
            """
            INSERT INTO schools (name, address, city, country, created_by)
            VALUES ('Linden Primary School', 'Lindenstrasse 4', 'Berlin', 'DE', $1)
            ON CONFLICT (name, city) DO UPDATE SET address = EXCLUDED.address
            RETURNING id
            """,
            parent_a["id"],
        )
        group = await conn.fetchrow(
            """
            INSERT INTO carpool_groups (school_id, name, created_by)
            VALUES ($1, 'Linden Year 3 Carpool', $2)
            ON CONFLICT DO NOTHING
            RETURNING id
            """,
            school["id"],
            parent_a["id"],
        )
        if group is None:
            group = await conn.fetchrow("SELECT id FROM carpool_groups WHERE name = 'Linden Year 3 Carpool'")
        for user_id, role in [(parent_a["id"], "creator"), (parent_b["id"], "member")]:
            await conn.execute(
                """
                INSERT INTO group_members (group_id, user_id, role)
                VALUES ($1, $2, $3)
                ON CONFLICT (group_id, user_id) DO UPDATE SET status = 'active'
                """,
                group["id"],
                user_id,
                role,
            )
        child_a = await conn.fetchrow(
            """
            INSERT INTO children (
                parent_id, school_id, name, year_group, pickup_notes,
                home_address, home_city, home_country, emergency_contact_name, emergency_contact_phone
            )
            VALUES ($1, $2, 'Mira Patel', 'Year 3', 'Waits by the main gate.', 'Kollwitzstrasse 52', 'Berlin', 'DE', 'Ava Patel', '+49 30 111111')
            ON CONFLICT DO NOTHING
            RETURNING id
            """,
            parent_a["id"],
            school["id"],
        ) or await conn.fetchrow("SELECT id FROM children WHERE name = 'Mira Patel'")
        child_b = await conn.fetchrow(
            """
            INSERT INTO children (
                parent_id, school_id, name, year_group, pickup_notes,
                home_address, home_city, home_country, emergency_contact_name, emergency_contact_phone
            )
            VALUES ($1, $2, 'Jonas Meyer', 'Year 3', 'Has violin on Thursdays.', 'Winsstrasse 12', 'Berlin', 'DE', 'Ben Meyer', '+49 30 222222')
            ON CONFLICT DO NOTHING
            RETURNING id
            """,
            parent_b["id"],
            school["id"],
        ) or await conn.fetchrow("SELECT id FROM children WHERE name = 'Jonas Meyer'")
        for child_id, driver_id, approver_id in [
            (child_a["id"], parent_a["id"], parent_a["id"]),
            (child_a["id"], parent_b["id"], parent_a["id"]),
            (child_b["id"], parent_a["id"], parent_b["id"]),
            (child_b["id"], parent_b["id"], parent_b["id"]),
        ]:
            await conn.execute(
                """
                INSERT INTO driver_approvals (group_id, child_id, driver_user_id, approved, approved_by)
                VALUES ($1, $2, $3, true, $4)
                ON CONFLICT (group_id, child_id, driver_user_id)
                DO UPDATE SET approved = true, approved_by = EXCLUDED.approved_by, updated_at = now()
                """,
                group["id"],
                child_id,
                driver_id,
                approver_id,
            )
        trip = await conn.fetchrow(
            """
            INSERT INTO trips (group_id, driver_user_id, service_date, trip_type, expected_time, created_by)
            VALUES ($1, $2, $3, 'pickup', '15:20', $4)
            RETURNING id
            """,
            group["id"],
            parent_a["id"],
            date.today() + timedelta(days=1),
            parent_a["id"],
        )
        for child_id in [child_a["id"], child_b["id"]]:
            await conn.execute(
                "INSERT INTO trip_children (trip_id, child_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                trip["id"],
                child_id,
            )
        print("Seeded SchoolHop dev data.")
        print("Login: ava@example.com / schoolhop-dev")
        print("Login: ben@example.com / schoolhop-dev")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
