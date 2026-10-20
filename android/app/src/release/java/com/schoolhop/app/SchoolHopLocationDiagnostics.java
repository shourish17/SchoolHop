package com.schoolhop.app;

import android.content.Context;
import com.getcapacitor.JSObject;

/** Release facade: no diagnostic logging or retained diagnostic state. */
final class SchoolHopLocationDiagnostics {
    static boolean isEnabled() { return false; }
    static void record(Context context, SchoolHopLocationTracker.Event event, int status,
            boolean active, long fixAgeMs, long uploadAgeMs) { }
    static JSObject snapshot(Context context) { return new JSObject(); }
}
