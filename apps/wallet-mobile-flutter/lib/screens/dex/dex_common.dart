// Shared plumbing for the DEX screens: a one-call "build → sign → broadcast"
// helper over the active account, plus small presentation widgets (capability
// banner, stat rows, USD/ANM formatting).

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../constants.dart';
import '../../models/account.dart';
import '../../services/deploy.dart' show deriveDeployedContractAddress;
import '../../services/dex.dart';
import '../../services/price.dart';
import '../../services/rpc.dart';
import '../../services/signer.dart';
import '../../state/dex_state.dart';
import '../../state/wallet_state.dart';

/// The outcome of a broadcast: the tx hash, and (for a deploy) the derived
/// contract address.
class TxOutcome {
  final String txHash;
  final String? contractAddress;
  const TxOutcome(this.txHash, {this.contractAddress});
}

/// Centralizes the chain-context + nonce + sign + broadcast dance so every DEX
/// screen doesn't reimplement it. All throw [RpcError]/[Exception] on failure
/// for the caller to surface.
class DexActions {
  final Ref _ref;
  DexActions(this._ref);

  RpcClient get _rpc => _ref.read(rpcProvider);

  Account _requireAccount() {
    final a = _ref.read(activeAccountProvider);
    if (a == null) throw StateError('No active account. Create or select a wallet first.');
    return a;
  }

  /// Deploy a bundled contract template (raw source + manifest). Returns the tx
  /// hash and the locally-derived contract address.
  Future<TxOutcome> deploy(ContractTemplate tpl) async {
    final acct = _requireAccount();
    final ctx = await chainContextFor(_rpc);
    final nonce = await _rpc.getPendingNonce(acct.address);
    final body = buildDeployBody(
      from: acct.address,
      code: tpl.codeBytes,
      manifest: tpl.manifestBytes,
      nonce: nonce,
      chainId: ctx.chainId,
    );
    final txHash = await signAndBroadcast(rpc: _rpc, account: acct, body: body, chainContext: ctx);
    final addr = deriveDeployedContractAddress(
      from: acct.address,
      chainId: ctx.chainId,
      nonce: nonce,
      code: tpl.codeBytes,
      manifest: tpl.manifestBytes,
    );
    return TxOutcome(txHash, contractAddress: addr);
  }

  /// A contract call (t=2) to [to] with pre-encoded [calldata].
  Future<TxOutcome> call({
    required String to,
    required Uint8List calldata,
    BigInt? value,
    int gasLimit = kDefaultCallGasLimit,
  }) async {
    final acct = _requireAccount();
    final ctx = await chainContextFor(_rpc);
    final nonce = await _rpc.getPendingNonce(acct.address);
    final body = buildCallBody(
      from: acct.address,
      to: to,
      calldata: calldata,
      nonce: nonce,
      value: value,
      gasLimit: gasLimit,
      chainId: ctx.chainId,
    );
    final txHash = await signAndBroadcast(rpc: _rpc, account: acct, body: body, chainContext: ctx);
    return TxOutcome(txHash);
  }

  /// A plain transfer (t=0), optionally carrying a [data] memo (used by promo).
  Future<TxOutcome> transferBody(Map<String, dynamic> body) async {
    final acct = _requireAccount();
    final ctx = await chainContextFor(_rpc);
    if (body['chainId'] != ctx.chainId) {
      throw StateError('tx chainId ${body['chainId']} != node ${ctx.chainId}');
    }
    final txHash = await signAndBroadcast(rpc: _rpc, account: acct, body: body, chainContext: ctx);
    return TxOutcome(txHash);
  }

  /// A prebuilt promo transfer to the foundation treasury.
  Future<TxOutcome> promote({required String contract, required int days, String label = ''}) async {
    final acct = _requireAccount();
    final ctx = await chainContextFor(_rpc);
    final nonce = await _rpc.getPendingNonce(acct.address);
    final body = _ref.read(dexServiceProvider).buildPromoTransfer(
          from: acct.address,
          contract: contract,
          days: days,
          nonce: nonce,
          label: label,
          chainId: ctx.chainId,
        );
    final txHash = await signAndBroadcast(rpc: _rpc, account: acct, body: body, chainContext: ctx);
    return TxOutcome(txHash);
  }
}

final dexActionsProvider = Provider<DexActions>((ref) => DexActions(ref));

/// An honest banner shown above execution-dependent DEX actions when the node
/// hasn't enabled contract execution (swaps/liquidity/launch-init revert there).
/// Transfers — including Promote — are unaffected and stay enabled.
class DexCapabilityBanner extends ConsumerWidget {
  /// When true, softens the copy to note that the specific action on this
  /// screen (e.g. Promote) still works regardless.
  final bool transferStillWorks;
  const DexCapabilityBanner({super.key, this.transferStillWorks = false});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    // On-chain trading goes live when the head reaches the FORK_VM_EXEC
    // activation height (network upgrade 9.6.0). Once live, hide the banner.
    final live = ref.watch(dexExecutionLiveProvider);
    if (live == true) return const SizedBox.shrink();

    final head = ref.watch(chainHeadProvider).value;
    final activation = AnimicaConfig.vmExecActivationHeight;
    final cs = Theme.of(context).colorScheme;

    final String message;
    if (head != null && head < activation) {
      final remaining = activation - head;
      final soon = remaining <= 1000;
      message = transferStillWorks
          ? 'On-chain swaps and liquidity go live at block $activation '
              '(upgrade 9.6.0) — ~$remaining blocks to go${soon ? ', soon' : ''}. '
              'Promoting a token is a treasury transfer and works today.'
          : 'On-chain trading (swaps, liquidity, token init) activates at block '
              '$activation — network upgrade 9.6.0, ~$remaining blocks away. '
              'You can deploy and prepare everything now; deploys and token '
              'promotion already settle.';
    } else {
      // Head unknown — keep the copy accurate without a countdown.
      message = transferStillWorks
          ? 'On-chain swaps and liquidity go live with network upgrade 9.6.0 '
              '(block $activation). Promoting a token works today.'
          : 'On-chain trading activates with network upgrade 9.6.0 (block '
              '$activation). Deploys and token promotion already settle.';
    }

    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: cs.tertiaryContainer,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Row(
        children: [
          Icon(Icons.rocket_launch_outlined, color: cs.onTertiaryContainer, size: 20),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              message,
              style: TextStyle(color: cs.onTertiaryContainer, fontSize: 12.5),
            ),
          ),
        ],
      ),
    );
  }
}

/// A label/value row with the value optionally in both ANM and USD.
class StatRow extends StatelessWidget {
  final String label;
  final String value;
  final String? sub; // e.g. the USD equivalent
  final Color? valueColor;
  const StatRow({super.key, required this.label, required this.value, this.sub, this.valueColor});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label, style: TextStyle(color: cs.outline, fontSize: 13)),
          const Spacer(),
          Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text(value,
                  style: TextStyle(
                      fontWeight: FontWeight.w600, fontSize: 14, color: valueColor)),
              if (sub != null)
                Text(sub!, style: TextStyle(color: cs.outline, fontSize: 12)),
            ],
          ),
        ],
      ),
    );
  }
}

/// Show a result/error snackbar + copy-hash affordance.
void showTxResult(BuildContext context, TxOutcome outcome) {
  ScaffoldMessenger.of(context).showSnackBar(
    SnackBar(
      content: Text('Submitted: ${_short(outcome.txHash)}'),
      action: SnackBarAction(
        label: 'Copy',
        onPressed: () => Clipboard.setData(ClipboardData(text: outcome.txHash)),
      ),
    ),
  );
}

void showTxError(BuildContext context, Object error) {
  final msg = error is RpcError ? error.message : error.toString();
  ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
}

String _short(String h) =>
    h.length <= 16 ? h : '${h.substring(0, 10)}…${h.substring(h.length - 4)}';

/// 1 token in ANM and USD, given the token's ANM price and the ANM/USD market.
String priceLine(double? priceAnm, AnmMarket? market) {
  if (priceAnm == null) return '—';
  final anm = '${_fmt(priceAnm)} ANM';
  if (market == null) return anm;
  return '$anm  ·  ${formatUsd(priceAnm * market.usdPerAnm, sigFigs: 3)}';
}

String anmAmount(double? v) => v == null ? '—' : '${_fmt(v)} ANM';

String _fmt(double v) {
  if (v == 0) return '0';
  if (v >= 1000) return v.toStringAsFixed(0);
  if (v >= 1) return v.toStringAsFixed(2);
  if (v >= 0.0001) return v.toStringAsFixed(4);
  return v.toStringAsExponential(2);
}
