// Native Serve & Earn engine — llama.cpp's llama-server, built for
// arm64-v8a with the Vulkan backend and bundled in the APK as
// jniLibs/arm64-v8a/libanmllamaserver.so (legacy jniLibs packaging extracts
// it to the app's nativeLibraryDir, the one place Android allows exec).
//
// The app spawns it on 127.0.0.1 and the worker loop talks plain
// OpenAI-compatible HTTP to it — the same architecture as the Termux
// animica-serve lane, which is how we know it holds up. GPU offload is
// requested with -ngl and VERIFIED from the server's own startup log
// ("offloaded N/M layers to GPU"); if Vulkan can't initialize on a device
// the engine restarts itself with -ngl 0 and serves on native CPU threads —
// still several times the WebView's single-thread WASM lane.

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:flutter/services.dart';
import 'package:path_provider/path_provider.dart';

class ServeModel {
  final String id;       // catalog id, mirrors the web worker's model ids
  final String label;
  final String file;     // filename on the pool mirror AND on disk
  final int bytes;       // exact size, for resume + integrity
  final String sha256;   // pool mirror copy's digest
  const ServeModel(this.id, this.label, this.file, this.bytes, this.sha256);

  String get url => 'https://pool.animica.org/downloads/models/$file';

  static const qwen05 = ServeModel(
    'qwen2.5-0.5b',
    'Qwen 2.5 0.5B (fast, ~0.5 GB)',
    'qwen2.5-0.5b-instruct-q4_k_m.gguf',
    491400032,
    '74a4da8c9fdbcd15bd1f6d01d621410d31c6fc00986f5eb687824e7b93d7a9db',
  );
  static const qwen15 = ServeModel(
    'qwen2.5-1.5b',
    'Qwen 2.5 1.5B (better answers, ~1.1 GB)',
    'qwen2.5-1.5b-instruct-q4_k_m.gguf',
    1117320736,
    '6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e',
  );
  static const all = [qwen05, qwen15];
}

class EngineStatus {
  final bool running;
  final bool gpu;            // true when layers actually offloaded to Vulkan
  final String detail;       // e.g. "Vulkan: Adreno (TM) 740 — 25/25 layers"
  final String? baseUrl;     // http://127.0.0.1:<port> when running
  const EngineStatus(this.running, this.gpu, this.detail, this.baseUrl);
}

class NativeEngine {
  static const _chan = MethodChannel('anm/native');

  Process? _proc;
  int? _port;
  bool _gpu = false;
  String _detail = '';
  final List<String> _log = [];

  String? get baseUrl => _port == null ? null : 'http://127.0.0.1:$_port';
  List<String> get recentLog =>
      _log.length <= 40 ? List.of(_log) : _log.sublist(_log.length - 40);

  /// The bundled server binary, or null when this build/device has none
  /// (non-Android, or a non-arm64 ABI where we don't ship it).
  Future<File?> binary() async {
    if (!Platform.isAndroid) return null;
    try {
      final dir = await _chan.invokeMethod<String>('nativeLibraryDir');
      if (dir == null) return null;
      final f = File('$dir/libanmllamaserver.so');
      return await f.exists() ? f : null;
    } catch (_) {
      return null;
    }
  }

  Future<Directory> _modelDir() async {
    final base = await getApplicationSupportDirectory();
    final d = Directory('${base.path}/models');
    await d.create(recursive: true);
    return d;
  }

  Future<File?> modelFileIfReady(ServeModel m) async {
    final f = File('${(await _modelDir()).path}/${m.file}');
    if (await f.exists() && await f.length() == m.bytes) return f;
    return null;
  }

  /// Resumable download from the pool's own mirror, then sha256-verified.
  /// onProgress gets 0..1.
  Future<File> ensureModel(ServeModel m, void Function(double) onProgress,
      {bool Function()? cancelled}) async {
    final ready = await modelFileIfReady(m);
    if (ready != null) return ready;
    final f = File('${(await _modelDir()).path}/${m.file}.part');
    var have = await f.exists() ? await f.length() : 0;
    if (have >= m.bytes) {
      await f.delete();
      have = 0;
    }
    final client = HttpClient();
    try {
      final req = await client.getUrl(Uri.parse(m.url));
      if (have > 0) req.headers.set('Range', 'bytes=$have-');
      final res = await req.close();
      if (res.statusCode != 200 && res.statusCode != 206) {
        throw HttpException('model download HTTP ${res.statusCode}');
      }
      if (res.statusCode == 200 && have > 0) {
        // Server ignored the Range — start over.
        await f.writeAsBytes(const []);
        have = 0;
      }
      final sink = f.openWrite(mode: FileMode.append);
      try {
        await for (final chunk in res) {
          if (cancelled?.call() == true) {
            throw const HttpException('cancelled');
          }
          sink.add(chunk);
          have += chunk.length;
          onProgress(have / m.bytes);
        }
      } finally {
        await sink.close();
      }
    } finally {
      client.close(force: true);
    }
    if (await f.length() != m.bytes) {
      throw HttpException(
          'model size mismatch: ${await f.length()} != ${m.bytes}');
    }
    final digest = await sha256.bind(f.openRead()).first;
    if (digest.toString() != m.sha256) {
      await f.delete();
      throw const HttpException('model sha256 mismatch — redownload');
    }
    final done = File('${(await _modelDir()).path}/${m.file}');
    await f.rename(done.path);
    return done;
  }

  /// Start (or restart) llama-server for [model]. Tries full GPU offload
  /// first; if the process dies within the warm-up window, retries on CPU.
  Future<EngineStatus> start(ServeModel model) async {
    await stop();
    final bin = await binary();
    if (bin == null) {
      return const EngineStatus(false, false, 'no native engine in this build', null);
    }
    final mf = await modelFileIfReady(model);
    if (mf == null) {
      return const EngineStatus(false, false, 'model not downloaded', null);
    }
    for (final ngl in const [999, 0]) {
      final ok = await _spawn(bin, mf, ngl);
      if (ok) {
        return EngineStatus(true, _gpu, _detail, baseUrl);
      }
    }
    return EngineStatus(false, false, 'engine failed to start: ${_log.isEmpty ? 'no output' : _log.last}', null);
  }

  Future<bool> _spawn(File bin, File model, int ngl) async {
    _log.clear();
    _gpu = false;
    _detail = ngl == 0 ? 'native CPU' : '';
    final sock = await ServerSocket.bind(InternetAddress.loopbackIPv4, 0);
    final port = sock.port;
    await sock.close();
    final proc = await Process.start(bin.path, [
      '-m', model.path,
      '--host', '127.0.0.1',
      '--port', '$port',
      '-ngl', '$ngl',
      '-c', '4096',
      '--threads', '${Platform.numberOfProcessors.clamp(2, 6)}',
      '--no-webui',
    ], environment: {
      'HOME': model.parent.path,
      'TMPDIR': model.parent.path,
    });
    _proc = proc;
    _port = port;
    void tap(Stream<List<int>> s) {
      s.transform(utf8.decoder).transform(const LineSplitter()).listen((l) {
        _log.add(l);
        if (_log.length > 400) _log.removeRange(0, 200);
        // "load_tensors: offloaded 25/25 layers to GPU"
        final m = RegExp(r'offloaded (\d+)/(\d+) layers to GPU').firstMatch(l);
        if (m != null && int.parse(m.group(1)!) > 0) {
          _gpu = true;
          _detail = 'Vulkan GPU — ${m.group(1)}/${m.group(2)} layers';
        }
        final d = RegExp(r'ggml_vulkan: \d+ = (.+?) \(').firstMatch(l);
        if (d != null) {
          _detail = '${d.group(1)}${_detail.isEmpty ? '' : ' — $_detail'}';
        }
      });
    }
    tap(proc.stdout);
    tap(proc.stderr);
    var exited = false;
    unawaited(proc.exitCode.then((_) => exited = true));
    // Warm-up: model load can take a while on a phone. Poll /health.
    final deadline = DateTime.now().add(const Duration(seconds: 150));
    final client = HttpClient()
      ..connectionTimeout = const Duration(seconds: 2);
    try {
      while (DateTime.now().isBefore(deadline)) {
        if (exited) return false;
        try {
          final req = await client
              .getUrl(Uri.parse('http://127.0.0.1:$port/health'));
          final res = await req.close();
          await res.drain<void>();
          if (res.statusCode == 200) {
            if (ngl == 0) _detail = 'native CPU';
            return true;
          }
        } catch (_) {/* not up yet */}
        await Future<void>.delayed(const Duration(milliseconds: 700));
      }
    } finally {
      client.close(force: true);
    }
    proc.kill(ProcessSignal.sigkill);
    return false;
  }

  Future<void> stop() async {
    final p = _proc;
    _proc = null;
    _port = null;
    if (p != null) {
      p.kill();
      await p.exitCode.timeout(const Duration(seconds: 3), onTimeout: () {
        p.kill(ProcessSignal.sigkill);
        return -1;
      });
    }
  }
}
