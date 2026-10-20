"""Test the actual deadline helper against Build #9's Google time backport.

Only the library package name is changed in temporary class files, keeping the
host JDK from silently supplying its more permissive java.time implementation.
This is a backport-library compatibility test, not an Android device test.
"""

import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile


LIBRARY_SHA256 = "d8044befae095781b9a80bf1faa92edc30382d75d437476784c1bf991598a976"


def main():
    repository = Path(__file__).resolve().parent.parent
    gradle = Path(os.environ.get("GRADLE_USER_HOME", Path.home() / ".gradle"))
    libraries = list((gradle / "caches/modules-2/files-2.1/com.android.tools/desugar_jdk_libs/2.1.5").glob("*/*.jar"))
    if len(libraries) != 1 or hashlib.sha256(libraries[0].read_bytes()).hexdigest() != LIBRARY_SHA256:
        raise SystemExit("Build #9 desugar_jdk_libs 2.1.5 artifact is missing or has an unexpected checksum")
    configuration = (repository / "android/app/build.gradle").read_text()
    if 'coreLibraryDesugaringEnabled true' not in configuration or 'com.android.tools:desugar_jdk_libs:2.1.5' not in configuration:
        raise SystemExit("Android desugaring configuration differs from Build #9")
    with tempfile.TemporaryDirectory(prefix="schoolhop-deadline-compat-") as temporary:
        root = Path(temporary)
        library = root / "isolated-time-library.jar"
        with zipfile.ZipFile(libraries[0]) as source, zipfile.ZipFile(library, "w") as output:
            for name in source.namelist():
                if name.startswith("java/time/") and name.endswith(".class"):
                    # Both names have equal byte lengths, preserving the class structure.
                    data = source.read(name).replace(b"java/time", b"test/time").replace(b"java.time", b"test.time")
                    output.writestr(name.replace("java/time/", "test/time/"), data)
        helper = root / "SchoolHopSafetyDeadline.java"
        actual_source = repository / "android/app/src/main/java/com/schoolhop/app/SchoolHopSafetyDeadline.java"
        helper.write_text(actual_source.read_text().replace("import java.time.", "import test.time."))
        probe = root / "DeadlineCompatibilityTest.java"
        probe.write_text('''package com.schoolhop.app;
public final class DeadlineCompatibilityTest {
    public static void main(String[] args) {
        String production = "2026-10-20T05:29:20.625772+00:00";
        String[] sameInstant = { production, "2026-10-20T05:29:20.625772Z",
            "2026-10-20T11:29:20.625772+06:00", "2026-10-20T00:29:20.625772-05:00",
            "2026-10-20T05:29:20.625+00:00", "2026-10-20T05:29:20.625772123+00:00" };
        for (String value : sameInstant) {
            if (SchoolHopSafetyDeadline.parseMillis(value) != 1792474160625L)
                throw new AssertionError("Valid offset deadline parsed incorrectly");
        }
        if (SchoolHopSafetyDeadline.parseMillis("2026-10-20T05:29:20+00:00") != 1792474160000L)
            throw new AssertionError("Whole-second deadline parsed incorrectly");
        for (String value : new String[] { "not-a-date", "", "2026-10-20T05:29:20.625772",
                "2026-02-30T05:29:20+00:00" }) {
            try { SchoolHopSafetyDeadline.parseMillis(value); throw new AssertionError("Invalid deadline accepted"); }
            catch (test.time.DateTimeException expected) { }
        }
        // Control: prove that this test uses the affected backport, not host java.time.
        try { test.time.Instant.parse(production); throw new AssertionError("Wrong Instant implementation selected"); }
        catch (test.time.format.DateTimeParseException expected) { }
        System.out.println("PASS: 12 deadline compatibility checks against Build #9 desugar_jdk_libs 2.1.5");
    }
}
''')
        subprocess.run(["javac", "-encoding", "UTF-8", "-cp", str(library), "-d", str(root), str(helper), str(probe)], check=True)
        subprocess.run(["java", "-cp", os.pathsep.join([str(root), str(library)]), "com.schoolhop.app.DeadlineCompatibilityTest"], check=True)
    print("Build #9 desugaring artifact SHA-256: " + LIBRARY_SHA256)


if __name__ == "__main__":
    main()
