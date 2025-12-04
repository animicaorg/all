/*
 * Animica Wallet — Accounts State (accounts, active account, balances)
 *
 * Responsibilities
 *  • Keep a list of accounts (watch-only by default; hook to Keyring later).
 *  • Track which account is active.
 *  • Fetch and cache balances + nonces via StateService.
 *  • Offer JSON hydrate/dehydrate so you can persist locally.
 *
 * Notes
 *  • Addresses are normalized to lowercase hex/bech32m strings.
 *  • This file purposely avoids storage/crypto — it just manages state.
 *    Wire to keyring/secure store from UI flows and call addAccount(...).
 */

import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:riverpod/riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../services/state_service.dart';
import 'providers.dart' show stateServiceProvider;

/// Simple account record. Use `meta` to stash per-account extras (e.g. path).
class WalletAccount {
  final String address;     // normalized
  final String label;       // user-visible name
  final bool watchOnly;     // true if no private key bound in this device
  final DateTime createdAt; // local timestamp
  final Map<String, dynamic> meta;

  const WalletAccount({
    required this.address,
    required this.label,
    this.watchOnly = true,
    required this.createdAt,
    this.meta = const {},
  });

  WalletAccount copyWith({
    String? address,
    String? label,
    bool? watchOnly,
    DateTime? createdAt,
    Map<String, dynamic>? meta,
  }) {
    return WalletAccount(
      address: address ?? this.address,
      label: label ?? this.label,
      watchOnly: watchOnly ?? this.watchOnly,
      createdAt: createdAt ?? this.createdAt,
      meta: meta ?? this.meta,
    );
  }

  Map<String, dynamic> toJson() => {
        'address': address,
        'label': label,
        'watchOnly': watchOnly,
        'createdAt': createdAt.toIso8601String(),
        'meta': meta,
      };

  factory WalletAccount.fromJson(Map<String, dynamic> m) => WalletAccount(
        address: (m['address'] ?? '').toString(),
        label: (m['label'] ?? '').toString(),
        watchOnly: _asBool(m['watchOnly'], true),
        createdAt: DateTime.tryParse((m['createdAt'] ?? '').toString()) ??
            DateTime.fromMillisecondsSinceEpoch(0, isUtc: true),
        meta: (m['meta'] is Map)
            ? (m['meta'] as Map).map(
                (k, v) => MapEntry(k.toString(), v),
              )
            : <String, dynamic>{},
      );

  @override
  String toString() => 'WalletAccount(${label.isEmpty ? address : label}:$address)';
}

/// Cached balance+nonce for an address.
class AccountBalance {
  final BigInt amount;          // native token (ANM) smallest unit
  final int nonce;              // tx nonce
  final DateTime lastUpdated;
  final bool loading;

  const AccountBalance({
    required this.amount,
    required this.nonce,
    required this.lastUpdated,
    this.loading = false,
  });

  AccountBalance copyWith({
    BigInt? amount,
    int? nonce,
    DateTime? lastUpdated,
    bool? loading,
  }) {
    return AccountBalance(
      amount: amount ?? this.amount,
      nonce: nonce ?? this.nonce,
      lastUpdated: lastUpdated ?? this.lastUpdated,
      loading: loading ?? this.loading,
    );
  }

  Map<String, dynamic> toJson() => {
        'amount': amount.toString(), // decimal
        'nonce': nonce,
        'lastUpdated': lastUpdated.toIso8601String(),
      };

  factory AccountBalance.fromJson(Map<String, dynamic> m) => AccountBalance(
        amount: BigInt.tryParse((m['amount'] ?? '0').toString()) ?? BigInt.zero,
        nonce: int.tryParse((m['nonce'] ?? '0').toString()) ?? 0,
        lastUpdated: DateTime.tryParse((m['lastUpdated'] ?? '').toString()) ??
            DateTime.fromMillisecondsSinceEpoch(0, isUtc: true),
      );
}

/// Global accounts state bag.
class AccountsState {
  final List<WalletAccount> accounts;
  final String? active; // address
  final Map<String, AccountBalance> balances; // by address
  final bool busy;
  final String? error;

  const AccountsState({
    this.accounts = const [],
    this.active,
    this.balances = const {},
    this.busy = false,
    this.error,
  });

  AccountsState copyWith({
    List<WalletAccount>? accounts,
    String? active,
    bool activeNull = false,
    Map<String, AccountBalance>? balances,
    bool? busy,
    String? error,
    bool errorNull = false,
  }) {
    return AccountsState(
      accounts: accounts ?? this.accounts,
      active: activeNull ? null : (active ?? this.active),
      balances: balances ?? this.balances,
      busy: busy ?? this.busy,
      error: errorNull ? null : (error ?? this.error),
    );
  }

  Map<String, dynamic> toJson() => {
        'accounts': accounts.map((a) => a.toJson()).toList(),
        'active': active,
        'balances': balances.map((k, v) => MapEntry(k, v.toJson())),
      };

  factory AccountsState.fromJson(Map<String, dynamic> m) {
    final accs = <WalletAccount>[];
    if (m['accounts'] is List) {
      for (final x in (m['accounts'] as List)) {
        if (x is Map) accs.add(WalletAccount.fromJson(x.cast<String, dynamic>()));
      }
    }
    final bals = <String, AccountBalance>{};
    if (m['balances'] is Map) {
      (m['balances'] as Map).forEach((k, v) {
        if (v is Map) {
          bals[k.toString()] = AccountBalance.fromJson(v.cast<String, dynamic>());
        }
      });
    }
    return AccountsState(
      accounts: accs,
      active: (m['active'] ?? '').toString().isEmpty ? null : (m['active'] as String),
      balances: bals,
    );
  }
}

/// Riverpod notifier handling accounts & balances.
class AccountsNotifier extends StateNotifier<AccountsState> {
  final Ref _ref;
  final Map<String, Future<void>?> _inflight = {}; // per-address refresh guard
  Timer? _poll;
  SharedPreferences? _prefs;

  static const _prefsKey = 'animica.accounts.v1';

  AccountsNotifier(this._ref) : super(const AccountsState()) {
    // Optionally: start a light poll for the active address balance every 8s.
    _poll = Timer.periodic(const Duration(seconds: 8), (_) {
      final a = state.active;
      if (a != null) refreshBalance(a);
    });

    _hydrateFromDisk();
  }

  // ---- account CRUD ----

  String _norm(String addr) {
    final t = addr.trim();
    if (t.startsWith('am')) return t.toLowerCase(); // bech32m HRP animica ("am")
    if (t.startsWith('0x') || t.startsWith('0X')) return '0x${t.substring(2).toLowerCase()}';
    return t.toLowerCase();
  }

  /// Add a watch-only account by address. Returns normalized address.
  String addAccount({required String address, String? label, bool watchOnly = true, Map<String, dynamic>? meta}) {
    final a = _norm(address);
    if (a.isEmpty) throw ArgumentError('address required');
    if (state.accounts.any((x) => x.address == a)) {
      // rename if label provided
      if (label != null && label.trim().isNotEmpty) {
        renameAccount(a, label.trim());
      }
      return a;
    }
    final entry = WalletAccount(
      address: a,
      label: (label ?? '').trim(),
      watchOnly: watchOnly,
      createdAt: DateTime.now().toUtc(),
      meta: meta ?? const {},
    );
    final next = [...state.accounts, entry];
    state = state.copyWith(accounts: next, errorNull: true);
    _persist();
    // If no active, set this one active
    if (state.active == null) {
      setActive(a);
    }
    return a;
  }

  void renameAccount(String address, String newLabel) {
    final a = _norm(address);
    final next = state.accounts
        .map((x) => x.address == a ? x.copyWith(label: newLabel.trim()) : x)
        .toList(growable: false);
    state = state.copyWith(accounts: next, errorNull: true);
    _persist();
  }

  void removeAccount(String address) {
    final a = _norm(address);
    final next = state.accounts.where((x) => x.address != a).toList(growable: false);
    final newActive = (state.active == a)
        ? (next.isNotEmpty ? next.first.address : null)
        : state.active;
    final bals = Map<String, AccountBalance>.from(state.balances)..remove(a);
    state = state.copyWith(accounts: next, active: newActive, balances: bals, errorNull: true, activeNull: newActive == null);
    _persist();
  }

  void setActive(String address) {
    final a = _norm(address);
    if (!state.accounts.any((x) => x.address == a)) {
      throw StateError('setActive: address not in account list');
    }
    state = state.copyWith(active: a, errorNull: true);
    _persist();
    // Proactively refresh active balance
    refreshBalance(a);
  }

  // ---- balances ----

  Future<void> refreshAllBalances() async {
    final addrs = state.accounts.map((x) => x.address).toList(growable: false);
    for (final a in addrs) {
      // Run sequentially to avoid hammering the RPC on low-power nodes.
      await refreshBalance(a);
    }
  }

  Future<void> refreshBalance(String address) async {
    final a = _norm(address);
    // De-dup refresh for same address if still in flight.
    final existing = _inflight[a];
    if (existing != null) return;
    final c = _refreshBalanceInternal(a);
    _inflight[a] = c;
    try {
      await c;
    } finally {
      _inflight.remove(a);
    }
  }

  Future<void> _refreshBalanceInternal(String a) async {
    final svc = _ref.read(stateServiceProvider);
    // Optimistic mark as loading
    final prev = state.balances[a];
    _setBalance(
      a,
      (prev ?? _emptyBal()).copyWith(loading: true, lastUpdated: DateTime.now().toUtc()),
    );
    try {
      final bal = await svc.getBalance(a); // BigInt or hex string (our service normalizes)
      final nonce = await svc.getNonce(a);
      _setBalance(
        a,
        AccountBalance(
          amount: _toBigInt(bal),
          nonce: nonce,
          lastUpdated: DateTime.now().toUtc(),
          loading: false,
        ),
      );
    } catch (e) {
      // retain previous but clear loading
      if (prev != null) {
        _setBalance(a, prev.copyWith(loading: false));
      } else {
        _setBalance(
          a,
          AccountBalance(
            amount: BigInt.zero,
            nonce: 0,
            lastUpdated: DateTime.now().toUtc(),
            loading: false,
          ),
        );
      }
      state = state.copyWith(error: 'balance refresh failed for $a: $e');
    }
  }

  AccountBalance _emptyBal() => AccountBalance(
        amount: BigInt.zero,
        nonce: 0,
        lastUpdated: DateTime.fromMillisecondsSinceEpoch(0, isUtc: true),
        loading: false,
      );

  void _setBalance(String address, AccountBalance b) {
    final map = Map<String, AccountBalance>.from(state.balances);
    map[address] = b;
    state = state.copyWith(balances: map);
  }

  // ---- persistence helpers ----

  AccountsState hydrate(Map<String, dynamic>? json) {
    if (json == null) return state;
    final next = AccountsState.fromJson(json);
    state = next;
    _persist();
    return next;
  }

  Map<String, dynamic> dehydrate() => state.toJson();

  @override
  void dispose() {
    _poll?.cancel();
    _inflight.clear();
    super.dispose();
  }

  // ---- persistence ----

  Future<SharedPreferences> _prefsInstance() async {
    _prefs ??= await SharedPreferences.getInstance();
    return _prefs!;
  }

  Future<void> _hydrateFromDisk() async {
    try {
      final prefs = await _prefsInstance();
      final raw = prefs.getString(_prefsKey);
      if (raw == null || raw.isEmpty) return;
      final decoded = jsonDecode(raw);
      if (decoded is Map<String, dynamic>) {
        hydrate(decoded);
      }
    } catch (_) {
      // Ignore persistence errors to avoid blocking app startup.
    }
  }

  Future<void> _persist() async {
    try {
      final prefs = await _prefsInstance();
      await prefs.setString(_prefsKey, jsonEncode(state.toJson()));
    } catch (_) {
      // Swallow persistence errors; state remains in memory.
    }
  }
}

// -------- Providers --------

/// Main accounts state provider.
final accountsStateProvider =
    StateNotifierProvider<AccountsNotifier, AccountsState>((ref) {
  return AccountsNotifier(ref);
});

/// Currently active account (or null).
final activeAccountProvider = Provider<WalletAccount?>((ref) {
  final s = ref.watch(accountsStateProvider);
  if (s.active == null) return null;
  final idx = s.accounts.indexWhere((a) => a.address == s.active);
  return idx >= 0 ? s.accounts[idx] : null;
});

/// Balance of the active account (or null).
final activeBalanceProvider = Provider<AccountBalance?>((ref) {
  final s = ref.watch(accountsStateProvider);
  final a = s.active;
  if (a == null) return null;
  return s.balances[a];
});

// -------- small utils --------

bool _asBool(dynamic v, bool fallback) {
  if (v is bool) return v;
  if (v == null) return fallback;
  final s = v.toString().toLowerCase();
  if (s == 'true' || s == '1' || s == 'yes' || s == 'y') return true;
  if (s == 'false' || s == '0' || s == 'no' || s == 'n') return false;
  return fallback;
}

BigInt _toBigInt(dynamic v) {
  if (v == null) return BigInt.zero;
  if (v is BigInt) return v;
  final s = v.toString().trim();
  if (s.isEmpty) return BigInt.zero;
  if (s.startsWith('0x') || s.startsWith('0X')) {
    final hex = s.substring(2);
    if (hex.isEmpty) return BigInt.zero;
    return BigInt.parse(hex, radix: 16);
    }
  return BigInt.tryParse(s) ?? BigInt.zero;
}
