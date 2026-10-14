package com.schoolhop.app;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class SchoolHopFirebaseConfigTest {
    @Test
    public void missingFirebaseValuesDisableMessaging() {
        assertFalse(SchoolHopFirebaseConfig.hasRequiredFirebaseValues("", ""));
        assertFalse(SchoolHopFirebaseConfig.hasRequiredFirebaseValues("1:android:abc", ""));
        assertFalse(SchoolHopFirebaseConfig.hasRequiredFirebaseValues("", "123456789"));
    }

    @Test
    public void configuredFirebaseValuesEnableMessaging() {
        assertTrue(SchoolHopFirebaseConfig.hasRequiredFirebaseValues("1:123456789:android:abcdef", "123456789"));
    }
}
