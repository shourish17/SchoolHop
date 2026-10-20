package com.schoolhop.app;

import java.time.OffsetDateTime;

/** The API emits ISO-8601 timestamps with explicit offsets and fractional seconds. */
final class SchoolHopSafetyDeadline {
    private SchoolHopSafetyDeadline() { }

    static long parseMillis(String value) {
        return OffsetDateTime.parse(value).toInstant().toEpochMilli();
    }
}
