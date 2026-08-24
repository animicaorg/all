package org.animica.animica_wallet

// local_auth needs the host Activity to be a FragmentActivity so it can show
// the system BiometricPrompt. FlutterFragmentActivity provides exactly that.
import io.flutter.embedding.android.FlutterFragmentActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterFragmentActivity() {
    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        // Serve & Earn's native engine execs the bundled llama-server from
        // the app's native library directory — the only location Android
        // allows exec from on API 29+ (jniLibs must use legacy packaging so
        // the binary is a real extracted file, see build.gradle.kts).
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "anm/native")
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "nativeLibraryDir" ->
                        result.success(applicationInfo.nativeLibraryDir)
                    else -> result.notImplemented()
                }
            }
    }
}
