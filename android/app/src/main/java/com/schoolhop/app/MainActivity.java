package com.schoolhop.app;

import android.os.Bundle;
import android.graphics.Color;
import android.view.View;
import androidx.activity.EdgeToEdge;
import androidx.activity.SystemBarStyle;
import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowInsetsCompat;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(SchoolHopConfigPlugin.class);
        registerPlugin(SchoolHopLocationPlugin.class);
        super.onCreate(savedInstanceState);

        // Use the same inset policy on older Android and enforced edge-to-edge on API 35/36.
        int background = Color.rgb(5, 5, 5);
        EdgeToEdge.enable(this, SystemBarStyle.dark(background), SystemBarStyle.dark(background));
        View content = findViewById(android.R.id.content);
        content.setBackgroundColor(background);
        ViewCompat.setOnApplyWindowInsetsListener(content, (view, windowInsets) -> {
            int safeTypes = WindowInsetsCompat.Type.systemBars() | WindowInsetsCompat.Type.displayCutout();
            Insets safe = windowInsets.getInsets(safeTypes);
            view.setPadding(safe.left, safe.top, safe.right, safe.bottom);

            // Native padding owns these insets. Zero them for the WebView to avoid
            // applying the same safe area twice; keep IME updates flowing to WebView.
            return new WindowInsetsCompat.Builder(windowInsets)
                .setInsets(safeTypes, Insets.NONE)
                .setInsetsIgnoringVisibility(safeTypes, Insets.NONE)
                .setDisplayCutout(null)
                .build();
        });
        ViewCompat.requestApplyInsets(content);
    }
}
