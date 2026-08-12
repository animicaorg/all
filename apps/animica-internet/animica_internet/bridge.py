"""
The window.animica provider exposed to .anm pages, backed by QWebChannel.

A per-tab WalletProvider QObject is published on a private QWebChannel; an injected user-script
builds window.animica in the page and marshals calls to it. Every method that could move value
or produce a signature routes through a fail-closed native approval dialog (wallet_ui) — the
approval channel is NOT reachable from page JS, so a hostile .anm site can never self-approve
(the extension's methodGate boundary, reproduced natively).

Return convention: slots return a JSON string {"ok":true,"result":...} | {"ok":false,"error":...};
the injected shim resolves/rejects the page promise accordingly.
"""

from __future__ import annotations

import json

from PySide6.QtCore import QObject, Slot
from PySide6.QtWebChannel import QWebChannel

from . import wallet_ui
from .wallet import Wallet, WalletError


def _ok(result):
    return json.dumps({"ok": True, "result": result})


def _fail(msg):
    return json.dumps({"ok": False, "error": str(msg)})


class WalletProvider(QObject):
    """Published to a single page. `origin_getter()` returns the page's .anm name for prompts."""

    def __init__(self, wallet: Wallet, origin_getter, parent=None):
        super().__init__(parent)
        self._wallet = wallet
        self._origin = origin_getter
        self._connected_origins: set[str] = set()

    @Slot(result=str)
    def isAnimica(self) -> str:  # noqa: N802
        return _ok(True)

    @Slot(result=str)
    def requestAccounts(self) -> str:  # noqa: N802
        origin = self._origin() or "unknown"
        try:
            addr = self._wallet.primary_address()
        except WalletError as e:
            return _fail(e)
        if origin not in self._connected_origins:
            if not wallet_ui.confirm(wallet_ui.ConnectApproveDialog, origin, addr):
                return _fail("user rejected connection")
            self._connected_origins.add(origin)
        return _ok([addr])

    @Slot(result=str)
    def accounts(self) -> str:
        origin = self._origin() or "unknown"
        if origin not in self._connected_origins:
            return _ok([])  # silent: not connected yet
        try:
            return _ok([self._wallet.primary_address()])
        except WalletError:
            return _ok([])

    @Slot(result=str)
    def getBalance(self) -> str:  # noqa: N802
        try:
            return _ok(str(self._wallet.get_balance_nanm()))
        except WalletError as e:
            return _fail(e)

    @Slot(str, result=str)
    def signMessage(self, message: str) -> str:  # noqa: N802
        origin = self._origin() or "unknown"
        if origin not in self._connected_origins:
            return _fail("connect the wallet first")
        try:
            addr = self._wallet.primary_address()
        except WalletError as e:
            return _fail(e)
        if not wallet_ui.confirm(wallet_ui.SignApproveDialog, origin, message, addr):
            return _fail("user rejected signature")
        try:
            sig_hex, _pub = self._wallet.sign_login(message, address=addr)
            return _ok(sig_hex)
        except WalletError as e:
            return _fail(e)

    @Slot(str, result=str)
    def sendTransaction(self, tx_json: str) -> str:  # noqa: N802
        origin = self._origin() or "unknown"
        if origin not in self._connected_origins:
            return _fail("connect the wallet first")
        try:
            tx = json.loads(tx_json or "{}")
        except ValueError:
            return _fail("invalid transaction object")
        to = str(tx.get("to") or "")
        amount = int(tx.get("amount") or tx.get("value") or 0)
        if not to or amount <= 0:
            return _fail("transaction needs a 'to' address and positive 'amount' (base units)")
        try:
            addr = self._wallet.primary_address()
        except WalletError as e:
            return _fail(e)
        if not wallet_ui.confirm(wallet_ui.SendApproveDialog, origin, to, amount, addr):
            return _fail("user rejected transaction")
        try:
            res = self._wallet.send(to, amount, from_address=addr, data_hex=tx.get("data"))
            return _ok(res.get("tx_hash") or res.get("txid") or res)
        except Exception as e:  # noqa: BLE001
            return _fail(e)


# Injected into every .anm page: build window.animica over the published channel object.
PROVIDER_SHIM = r"""
(function () {
  if (window.__animicaInjected) return;
  window.__animicaInjected = true;
  new QWebChannel(qt.webChannelTransport, function (channel) {
    var p = channel.objects.animicaProvider;
    function call(fn, arg) {
      return new Promise(function (resolve, reject) {
        var cb = function (json) {
          var r;
          try { r = JSON.parse(json); } catch (e) { return reject(new Error('bad provider response')); }
          if (r && r.ok) resolve(r.result); else reject(new Error((r && r.error) || 'request failed'));
        };
        if (arg === undefined) fn(cb); else fn(arg, cb);
      });
    }
    window.animica = {
      isAnimica: true,
      request: function (req) {
        req = req || {};
        var m = req.method, params = req.params || [];
        switch (m) {
          case 'animica_requestAccounts':
          case 'eth_requestAccounts': return call(p.requestAccounts);
          case 'animica_accounts':
          case 'eth_accounts': return call(p.accounts);
          case 'animica_getBalance': return call(p.getBalance);
          case 'animica_signMessage':
          case 'personal_sign': return call(p.signMessage, (params[0] && params[0].message) || params[0]);
          case 'animica_sendTransaction':
          case 'eth_sendTransaction': return call(p.sendTransaction, JSON.stringify(params[0] || {}));
          default: return Promise.reject(new Error('unsupported method: ' + m));
        }
      },
      requestAccounts: function () { return call(p.requestAccounts); },
      getAddress: function () { return call(p.requestAccounts).then(function (a) { return a[0]; }); },
      getBalance: function () { return call(p.getBalance); },
      signMessage: function (msg) { return call(p.signMessage, msg); },
      sendTransaction: function (tx) { return call(p.sendTransaction, JSON.stringify(tx || {})); }
    };
    window.dispatchEvent(new Event('animica#initialized'));
  });
})();
"""


def make_channel(wallet: Wallet, origin_getter, parent=None) -> tuple[QWebChannel, WalletProvider]:
    provider = WalletProvider(wallet, origin_getter, parent=parent)
    channel = QWebChannel(parent)
    channel.registerObject("animicaProvider", provider)
    return channel, provider
