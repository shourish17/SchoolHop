package com.schoolhop.app;

import static org.junit.Assert.*;
import java.time.DateTimeException;
import org.junit.Test;

public class SchoolHopSafetyDeadlineTest {
    @Test public void exactProductionTimestampSupportsMicrosecondsAndUtcOffset() {
        assertEquals(1792474160625L, SchoolHopSafetyDeadline.parseMillis("2026-10-20T05:29:20.625772+00:00"));
    }

    @Test public void equivalentOffsetsAndFractionWidthsIdentifyTheSameInstant() {
        for (String value : new String[] {
                "2026-10-20T05:29:20.625772Z",
                "2026-10-20T11:29:20.625772+06:00",
                "2026-10-20T00:29:20.625772-05:00",
                "2026-10-20T05:29:20.625+00:00",
                "2026-10-20T05:29:20.625772123+00:00" }) {
            assertEquals(value, 1792474160625L, SchoolHopSafetyDeadline.parseMillis(value));
        }
        assertEquals(1792474160000L, SchoolHopSafetyDeadline.parseMillis("2026-10-20T05:29:20+00:00"));
    }

    @Test public void malformedOrOffsetFreeDeadlinesAreRejected() {
        for (String value : new String[] { "not-a-date", "", "2026-10-20T05:29:20.625772", "2026-02-30T05:29:20+00:00" }) {
            try { SchoolHopSafetyDeadline.parseMillis(value); fail("Invalid deadline accepted"); }
            catch (DateTimeException expected) { /* Fail closed; never log the supplied value. */ }
        }
    }
}
