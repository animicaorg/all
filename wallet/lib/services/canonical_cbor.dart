// Simple canonical JSON helper for Dart used to create deterministic signing bytes
// (development). This recursively sorts map keys to produce stable serialization.

import 'dart:convert';

String _canonicalJsonString(dynamic v) {
  if (v is Map) {
    final keys = v.keys.cast<String>().toList()..sort();
    final pairs = keys.map((k) => '"' + jsonEncode(k).replaceAll('"', '') + '":' + _canonicalJsonString(v[k]));
    return '{' + pairs.join(',') + '}';
  } else if (v is List) {
    return '[' + v.map((e) => _canonicalJsonString(e)).join(',') + ']';
  } else {
    return jsonEncode(v);
  }
}

List<int> canonicalBytes(dynamic obj) {
  return utf8.encode(_canonicalJsonString(obj));
}
