"""
Side-panel dialogs: Directory (search), Reserve (pay ANM to the Foundation to reserve a name),
Publish (upload a self-contained HTML site to a name you own), and Wallet (address/balance).

Network calls run on a worker thread so the UI never freezes; results post back via signals.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QSpinBox, QVBoxLayout)

from . import names, serve
from .config import NANM_PER_ANM
from .registry_client import RegistryClient
from .wallet import Wallet, WalletError


class _Worker(QRunnable):
    class _Sig(QObject):
        done = Signal(object)
        fail = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.sig = self._Sig()

    def run(self):
        try:
            self.sig.done.emit(self.fn())
        except Exception as e:  # noqa: BLE001
            self.sig.fail.emit(str(e))


def _run(fn, on_done, on_fail):
    w = _Worker(fn)
    w.sig.done.connect(on_done)
    w.sig.fail.connect(on_fail)
    QThreadPool.globalInstance().start(w)


class DirectoryPanel(QDialog):
    def __init__(self, open_name, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Animica Internet — Directory")
        self.setMinimumSize(520, 460)
        self._open = open_name
        self._reg = RegistryClient()
        v = QVBoxLayout(self)
        row = QHBoxLayout()
        self.q = QLineEdit()
        self.q.setPlaceholderText("Search .anm sites…")
        self.q.returnPressed.connect(self.search)
        btn = QPushButton("Search")
        btn.clicked.connect(self.search)
        row.addWidget(self.q)
        row.addWidget(btn)
        v.addLayout(row)
        self.results = QListWidget()
        self.results.itemActivated.connect(self._pick)
        v.addWidget(self.results)
        self.search()

    def search(self):
        self.results.clear()
        self.results.addItem("Searching…")
        _run(lambda: self._reg.search(self.q.text()), self._show, self._err)

    def _show(self, rows):
        self.results.clear()
        if not rows:
            self.results.addItem("No sites found.")
            return
        for r in rows:
            name = r.get("name") or r.get("fqdn", "")
            it = QListWidgetItem(f"{name}.anm   ·   {r.get('kind', '')}")
            it.setData(Qt.ItemDataRole.UserRole, name)
            self.results.addItem(it)

    def _err(self, msg):
        self.results.clear()
        self.results.addItem(f"Error: {msg}")

    def _pick(self, item):
        name = item.data(Qt.ItemDataRole.UserRole)
        if name:
            self._open(name)
            self.accept()


class ReservePanel(QDialog):
    def __init__(self, wallet: Wallet, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Reserve a .anm name")
        self.setMinimumWidth(460)
        self.wallet = wallet
        self._reg = RegistryClient()
        v = QVBoxLayout(self)
        v.addWidget(QLabel("Reserve a name on the Animica Internet. The fee is paid in ANM to "
                           "the Animica Foundation on-chain."))
        row = QHBoxLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("yourname")
        self.name.textChanged.connect(self._quote)
        row.addWidget(self.name)
        row.addWidget(QLabel(".anm"))
        v.addLayout(row)
        yr = QHBoxLayout()
        yr.addWidget(QLabel("Years:"))
        self.years = QSpinBox()
        self.years.setRange(1, 10)
        self.years.valueChanged.connect(self._quote)
        yr.addWidget(self.years)
        yr.addStretch()
        v.addLayout(yr)
        self.quote = QLabel("")
        self.quote.setWordWrap(True)
        v.addWidget(self.quote)
        self.btn = QPushButton("Pay & reserve")
        self.btn.clicked.connect(self._reserve)
        v.addWidget(self.btn)
        self._quote()

    def _quote(self):
        try:
            q = names.reservation_quote(self.name.text() or "x", self.years.value())
            self.quote.setText(f"Fee: <b>{q['feeAnm']} ANM</b> for {q['years']} year(s) → "
                               f"Foundation <code>{q['foundation'][:16]}…</code>")
        except names.ReserveError as e:
            self.quote.setText(f"<span style='color:#F0A020'>{e}</span>")

    def _reserve(self):
        self.btn.setEnabled(False)
        self.btn.setText("Reserving…")
        name, years = self.name.text(), self.years.value()

        def work():
            try:
                return names.reserve(self.wallet, self._reg, name, years=years)
            except names.InsufficientBalance as e:
                return {"_insufficient": True, "message": str(e),
                        "deposit": e.deposit_address, "feeAnm": e.fee_anm}

        _run(work, self._ok, self._err)

    def _ok(self, out):
        self.btn.setEnabled(True)
        self.btn.setText("Pay & reserve")
        if out.get("_insufficient"):
            # Offer to fund the marketplace balance from the wallet ("pay in the browser").
            reply = QMessageBox.question(
                self, "Fund your balance",
                out["message"] + f"\n\nSend {out['feeAnm']} ANM from your wallet to your deposit "
                f"address now?\n({out.get('deposit') or 'deposit address unavailable'})")
            if reply == QMessageBox.StandardButton.Yes and out.get("deposit"):
                _run(lambda: names.fund_balance(self.wallet, self._reg, out["feeAnm"]),
                     lambda r: QMessageBox.information(
                         self, "Funding sent",
                         f"Sent {out['feeAnm']} ANM (tx {str(r.get('txid'))[:18]}…). It credits your "
                         f"balance after ~12 confirmations — then reserve again."),
                     lambda m: QMessageBox.warning(self, "Funding failed", m))
            return
        QMessageBox.information(self, "Reserved",
                                f"{out['name']}.anm is now yours ({out['feeAnm']} ANM → Foundation).\n"
                                f"Use Publish to add a site.")
        self.accept()

    def _err(self, msg):
        self.btn.setEnabled(True)
        self.btn.setText("Pay & reserve")
        QMessageBox.warning(self, "Reservation failed", msg)


class PublishPanel(QDialog):
    def __init__(self, wallet: Wallet, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Publish a site to the Animica Internet")
        self.setMinimumWidth(480)
        self.wallet = wallet
        self._reg = RegistryClient()
        self._html = ""
        v = QVBoxLayout(self)
        v.addWidget(QLabel("Publish a single self-contained HTML file (≤ 2 MB) to a name you own."))
        row = QHBoxLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("yourname")
        row.addWidget(self.name)
        row.addWidget(QLabel(".anm"))
        v.addLayout(row)
        pick = QPushButton("Choose HTML file / folder…")
        pick.clicked.connect(self._pick)
        v.addWidget(pick)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        v.addWidget(self.status)
        self.btn = QPushButton("Publish")
        self.btn.clicked.connect(self._publish)
        v.addWidget(self.btn)

    def _pick(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose a self-contained HTML site",
                                              filter="HTML (*.html *.htm)")
        if not path:
            return
        try:
            self._html = serve.load_site_html(path)
            self.status.setText(f"Loaded {len(self._html.encode()):,} bytes · "
                                f"CID {serve.local_cid(self._html)[:20]}…")
        except serve.PublishError as e:
            self.status.setText(f"<span style='color:#F0A020'>{e}</span>")

    def _publish(self):
        if not self._html:
            self.status.setText("Choose a file first.")
            return
        name = self.name.text()
        self.btn.setEnabled(False)
        self.btn.setText("Publishing…")

        def work():
            self._reg.login(self.wallet)             # wallet-signed session
            return serve.publish_site(self._reg, name, self._html)

        _run(work, self._ok, self._err)

    def _ok(self, res):
        QMessageBox.information(self, "Published",
                                f"Live at {res.get('fqdn', self.name.text() + '.anm')}\n"
                                f"CID {res.get('cid', '')}")
        self.accept()

    def _err(self, msg):
        self.btn.setEnabled(True)
        self.btn.setText("Publish")
        QMessageBox.warning(self, "Publish failed", msg)


class WalletPanel(QDialog):
    def __init__(self, wallet: Wallet, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Wallet")
        self.setMinimumWidth(440)
        self.wallet = wallet
        v = QVBoxLayout(self)
        self.addr = QLabel("…")
        self.addr.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.addr.setWordWrap(True)
        self.bal = QLabel("…")
        v.addWidget(QLabel("<b>Address</b>"))
        v.addWidget(self.addr)
        v.addWidget(QLabel("<b>Balance</b>"))
        v.addWidget(self.bal)
        create = QPushButton("Create wallet")
        create.clicked.connect(self._create)
        v.addWidget(create)
        self._refresh()

    def _refresh(self):
        try:
            addr = self.wallet.primary_address()
            self.addr.setText(addr)
            _run(lambda: self.wallet.get_balance_nanm(addr),
                 lambda n: self.bal.setText(f"{n / NANM_PER_ANM:,.9f} ANM"),
                 lambda e: self.bal.setText(f"(balance error: {e})"))
        except WalletError:
            self.addr.setText("No wallet yet — create one.")
            self.bal.setText("—")

    def _create(self):
        try:
            acct = self.wallet.create()
            QMessageBox.information(self, "Wallet created", f"Address:\n{acct.address}")
            self._refresh()
        except WalletError as e:
            QMessageBox.warning(self, "Error", str(e))
