/*
 * Animica Wallet — Minimal ABI Types (fn/event/error)
 *
 * Goals:
 *  • Represent a simple ABI model compatible with Animica VM contracts.
 *  • Keep types minimal: u256, bool, bytes, string, address, bytesN (N=1..32).
 *  • Provide helpers to compute:
 *      - function selector: keccak256("name(type,...)")[0..4)
 *      - event topic:       keccak256("name(type,...)") (32 bytes)
 *      - error selector:    keccak256("name(type,...)")[0..4)
 *  • Parse from a JSON-like ABI (list of items with {type,name,inputs,outputs}).
 *
 * Encoding of arguments is intentionally NOT done here (chains differ).
 * This module focuses on types, validation, and signature hashing only.
 */

import 'dart:convert' show utf8;
import 'dart:typed_data';

import '../crypto/sha3.dart' as hash;
import '../crypto/bech32m.dart' show AnimicaAddr;

/// Kinds of ABI items.
enum AbiItemKind { function, event, error }

/// Canonical scalar/aggregate types supported by this minimal ABI.
class AbiType {
  final String canonical; // e.g. "u256", "bool", "bytes", "bytes32", "string", "address"
  final int? fixedBytesLen; // for bytesN (1..32)

  const AbiType._(this.canonical, {this.fixedBytesLen});

  static const AbiType u256 = AbiType._('u256');
  static const AbiType boolean = AbiType._('bool');
  static const AbiType bytes = AbiType._('bytes');
  static const AbiType string = AbiType._('string');
  static const AbiType address = AbiType._('address');

  static AbiType bytesN(int n) {
    if (n < 1 || n > 32) {
      throw ArgumentError.value(n, 'n', 'bytesN must be 1..32');
    }
    return AbiType._('bytes$n', fixedBytesLen: n);
  }

  /// Parse from canonical string.
  static AbiType? parse(String s) {
    final t = s.trim().toLowerCase();
    if (t == 'u256') return u256;
    if (t == 'bool') return boolean;
    if (t == 'bytes') return bytes;
    if (t == 'string') return string;
    if (t == 'address') return address;
    final m = RegExp(r'^bytes([1-9]|[12]\d|3[0-2])$').firstMatch(t);
    if (m != null) {
      final n = int.parse(m.group(1)!);
      return bytesN(n);
    }
    return null;
  }

  bool get isDynamic => (this == bytes || this == string);
  bool get isFixedBytes => fixedBytesLen != null;

  @override
  String toString() => canonical;

  /// Dart value guard (lightweight).
  bool accepts(Object? v) {
    if (this == u256) {
      return v is BigInt && v >= BigInt.zero && v <= _maxU256;
    }
    if (this == boolean) return v is bool;
    if (this == string) return v is String;
    if (this == address) {
      if (v is! String) return false;
      return AnimicaAddr.isValid(v);
    }
    if (this == bytes) return v is Uint8List || (v is List<int> && _allByte(v));
    if (isFixedBytes) {
      final n = fixedBytesLen!;
      if (v is Uint8List) return v.length == n;
      if (v is List<int> && _allByte(v)) return v.length == n;
      return false;
    }
    return false;
  }
}

final _maxU256 = (BigInt.one << 256) - BigInt.one;
bool _allByte(List<int> xs) => xs.every((e) => e >= 0 && e <= 255);

/// ABI parameter (used for functions/events/errors).
class AbiParam {
  final String name;
  final AbiType type;
  final bool indexed; // only meaningful for events

  AbiParam({required this.name, required this.type, this.indexed = false});

  factory AbiParam.fromJson(Map<String, dynamic> j, {bool forEvent = false}) {
    final t = AbiType.parse(j['type'] as String? ?? '') ??
        (throw ArgumentError('Unknown type in ABI param: ${j['type']}'));
    final nm = (j['name'] as String? ?? '').trim();
    final ix = forEvent ? (j['indexed'] as bool? ?? false) : false;
    return AbiParam(name: nm, type: t, indexed: ix);
  }

  Map<String, dynamic> toJson() => {
        'name': name,
        'type': type.canonical,
        if (indexed) 'indexed': true,
      };

  @override
  String toString() => '${type.canonical} $name${indexed ? " indexed" : ""}';
}

/// Base for function/event/error.
abstract class AbiItem {
  final AbiItemKind kind;
  final String name;
  final List<AbiParam> inputs;

  AbiItem(this.kind, this.name, this.inputs);

  /// Canonical signature like:  name(u256,address,bytes)
  String signature() {
    final types = inputs.map((p) => p.type.canonical).join(',');
    return '$name($types)';
  }

  /// Keccak256(signature).
  Uint8List signatureHash() => hash.keccak256(utf8.encode(signature()));

  /// First 4 bytes of keccak(signature) — for functions/errors.
  Uint8List selector4() => Uint8List.sublistView(signatureHash(), 0, 4);

  @override
  String toString() => '${kind.name} $signature()';
}

class FunctionAbi extends AbiItem {
  final List<AbiParam> outputs;
  final String stateMutability; // e.g., "view", "pure", "nonpayable", "payable" (free-form)

  FunctionAbi({
    required String name,
    required List<AbiParam> inputs,
    List<AbiParam>? outputs,
    this.stateMutability = 'nonpayable',
  })  : outputs = outputs ?? const [],
        super(AbiItemKind.function, name, inputs);

  factory FunctionAbi.fromJson(Map<String, dynamic> j) {
    final inputs = ((j['inputs'] as List?) ?? const [])
        .map((e) => AbiParam.fromJson(Map<String, dynamic>.from(e as Map), forEvent: false))
        .toList();
    final outputs = ((j['outputs'] as List?) ?? const [])
        .map((e) => AbiParam.fromJson(Map<String, dynamic>.from(e as Map), forEvent: false))
        .toList();
    final mut = (j['stateMutability'] as String?)?.trim() ?? 'nonpayable';
    return FunctionAbi(
      name: (j['name'] as String? ?? '').trim(),
      inputs: inputs,
      outputs: outputs,
      stateMutability: mut,
    );
  }

  Map<String, dynamic> toJson() => {
        'type': 'function',
        'name': name,
        'stateMutability': stateMutability,
        'inputs': inputs.map((p) => p.toJson()).toList(),
        'outputs': outputs.map((p) => p.toJson()).toList(),
      };
}

class EventAbi extends AbiItem {
  final bool anonymous;

  EventAbi({
    required String name,
    required List<AbiParam> inputs,
    this.anonymous = false,
  }) : super(AbiItemKind.event, name, inputs);

  /// Topic0 for non-anonymous events = keccak256(signature).
  Uint8List topic0() => signatureHash();

  factory EventAbi.fromJson(Map<String, dynamic> j) {
    final inputs = ((j['inputs'] as List?) ?? const [])
        .map((e) => AbiParam.fromJson(Map<String, dynamic>.from(e as Map), forEvent: true))
        .toList();
    final anon = j['anonymous'] as bool? ?? false;
    return EventAbi(
      name: (j['name'] as String? ?? '').trim(),
      inputs: inputs,
      anonymous: anon,
    );
  }

  Map<String, dynamic> toJson() => {
        'type': 'event',
        'name': name,
        'anonymous': anonymous,
        'inputs': inputs.map((p) => p.toJson()).toList(),
      };
}

class ErrorAbi extends AbiItem {
  ErrorAbi({
    required String name,
    required List<AbiParam> inputs,
  }) : super(AbiItemKind.error, name, inputs);

  /// Error selector (first 4 bytes).
  Uint8List selector() => selector4();

  factory ErrorAbi.fromJson(Map<String, dynamic> j) {
    final inputs = ((j['inputs'] as List?) ?? const [])
        .map((e) => AbiParam.fromJson(Map<String, dynamic>.from(e as Map), forEvent: false))
        .toList();
    return ErrorAbi(
      name: (j['name'] as String? ?? '').trim(),
      inputs: inputs,
    );
  }

  Map<String, dynamic> toJson() => {
        'type': 'error',
        'name': name,
        'inputs': inputs.map((p) => p.toJson()).toList(),
      };
}

/// Contract ABI wrapper with lookup utilities.
class ContractAbi {
  final List<FunctionAbi> functions;
  final List<EventAbi> events;
  final List<ErrorAbi> errors;

  ContractAbi({
    required this.functions,
    required this.events,
    required this.errors,
  });

  factory ContractAbi.fromJson(List<dynamic> json) {
    final fns = <FunctionAbi>[];
    final evs = <EventAbi>[];
    final ers = <ErrorAbi>[];
    for (final it in json) {
      final m = Map<String, dynamic>.from(it as Map);
      final t = (m['type'] as String? ?? 'function').toLowerCase();
      if (t == 'function') {
        fns.add(FunctionAbi.fromJson(m));
      } else if (t == 'event') {
        evs.add(EventAbi.fromJson(m));
      } else if (t == 'error') {
        ers.add(ErrorAbi.fromJson(m));
      }
    }
    return ContractAbi(functions: fns, events: evs, errors: ers);
  }

  /// Find a function by name and arity (number of inputs).
  FunctionAbi? fn(String name, {int? arity}) {
    for (final f in functions) {
      if (f.name == name && (arity == null || f.inputs.length == arity)) return f;
    }
    return null;
  }

  /// Find event by name and arity.
  EventAbi? event(String name, {int? arity}) {
    for (final e in events) {
      if (e.name == name && (arity == null || e.inputs.length == arity)) return e;
    }
    return null;
  }

  /// Find error by name and arity.
  ErrorAbi? error(String name, {int? arity}) {
    for (final e in errors) {
      if (e.name == name && (arity == null || e.inputs.length == arity)) return e;
    }
    return null;
  }

  Map<String, dynamic> toJson() => {
        'functions': functions.map((f) => f.toJson()).toList(),
        'events': events.map((e) => e.toJson()).toList(),
        'errors': errors.map((e) => e.toJson()).toList(),
      };
}
