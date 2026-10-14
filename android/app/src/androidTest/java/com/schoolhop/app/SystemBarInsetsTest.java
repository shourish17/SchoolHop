package com.schoolhop.app;

import static org.junit.Assert.*;

import android.content.Intent;
import android.view.View;
import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowInsetsCompat;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import org.junit.Test;
import org.junit.runner.RunWith;

@RunWith(AndroidJUnit4.class)
public class SystemBarInsetsTest {
    @Test
    public void containerHandlesInsetsOnceAndPreservesKeyboardUpdates() {
        var instrumentation = InstrumentationRegistry.getInstrumentation();
        var context = instrumentation.getTargetContext();
        Intent intent = new Intent(context, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        MainActivity activity = (MainActivity) instrumentation.startActivitySync(intent);
        instrumentation.waitForIdleSync();
        try {
            instrumentation.runOnMainSync(() -> {
                View content = activity.findViewById(android.R.id.content);
                int safeTypes = WindowInsetsCompat.Type.systemBars() | WindowInsetsCompat.Type.displayCutout();
                // Portrait, landscape with a side cutout/nav bar, larger display, then no bars.
                for (Insets safe : new Insets[] {
                    Insets.of(0, 80, 0, 72), Insets.of(100, 24, 72, 0),
                    Insets.of(0, 120, 0, 96), Insets.NONE
                }) {
                    WindowInsetsCompat input = new WindowInsetsCompat.Builder()
                        .setInsets(safeTypes, safe)
                        .setInsets(WindowInsetsCompat.Type.ime(), Insets.of(0, 0, 0, 600))
                        .setVisible(safeTypes | WindowInsetsCompat.Type.ime(), true)
                        .build();
                    WindowInsetsCompat remaining = ViewCompat.dispatchApplyWindowInsets(content, input);
                    assertEquals(safe.left, content.getPaddingLeft());
                    assertEquals(safe.top, content.getPaddingTop());
                    assertEquals(safe.right, content.getPaddingRight());
                    assertEquals(safe.bottom, content.getPaddingBottom());
                    assertEquals(Insets.NONE, remaining.getInsets(safeTypes));
                    assertEquals(600, remaining.getInsets(WindowInsetsCompat.Type.ime()).bottom);
                }
            });
        } finally {
            instrumentation.runOnMainSync(activity::finish);
        }
    }
}
