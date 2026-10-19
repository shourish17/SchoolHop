package com.schoolhop.app;

import android.content.Context;
import com.getcapacitor.JSObject;

/** Release builds contain no diagnostic implementation, logging, or message interception. */
final class SchoolHopPushDiagnostics {
    private SchoolHopPushDiagnostics() {}
    static boolean isEnabled() { return false; }
    static void log(Context context, String event) {}
    static JSObject snapshot(Context context) {
        throw new UnsupportedOperationException("Debug builds only");
    }
}
