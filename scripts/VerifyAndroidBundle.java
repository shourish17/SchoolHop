import java.io.InputStream;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.jar.JarFile;

/** Verify every payload entry, including its signer; unsigned additions fail closed. */
class VerifyAndroidBundle {
    public static void main(String[] args) throws Exception {
        String expected = System.getenv("ANDROID_UPLOAD_CERT_SHA256");
        int checked = 0;
        try (JarFile jar = new JarFile(Path.of(args[0]).toFile(), true)) {
            var entries = jar.entries();
            while (entries.hasMoreElements()) {
                var entry = entries.nextElement();
                if (entry.isDirectory()) continue;
                try (InputStream stream = jar.getInputStream(entry)) {
                    stream.transferTo(java.io.OutputStream.nullOutputStream());
                }
                if (entry.getName().startsWith("META-INF/")) continue;
                var signers = entry.getCodeSigners();
                if (signers == null || signers.length != 1) {
                    throw new SecurityException("Missing or multiple payload signers");
                }
                var cert = signers[0].getSignerCertPath().getCertificates().get(0);
                String actual = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(cert.getEncoded()));
                if (!actual.equals(expected)) throw new SecurityException("Unexpected upload signer");
                ((java.security.cert.X509Certificate) cert).checkValidity();
                checked++;
            }
        }
        if (checked == 0) throw new SecurityException("Empty bundle");
        System.out.println("Verified " + checked + " signed payload entries against upload certificate " + expected);
    }
}
