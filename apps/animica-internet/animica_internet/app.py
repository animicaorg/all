"""
The Animica Internet browser window.

A .anm-ONLY browser: the address bar resolves <name>.anm through the registry and loads either
CID-verified content (served by the anm:// scheme handler) or an endpoint-record target. It
never navigates arbitrary clearnet URLs typed by the user. Each tab renders in its own ephemeral
profile with the injected window.animica wallet provider.
"""

from __future__ import annotations

from PySide6.QtCore import QFile, QIODevice, QUrl, Signal
from PySide6.QtGui import QAction
from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineScript
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (QLineEdit, QMainWindow, QPushButton,
                               QTabWidget, QToolBar, QVBoxLayout, QWidget)

from . import bridge, resolver
from .config import ANM_SCHEME, APP_NAME, HOME_NAME
from .scheme import AnmSchemeHandler
from .wallet import Wallet
from .panels import DirectoryPanel, PublishPanel, ReservePanel, WalletPanel


def _qwebchannel_js() -> str:
    f = QFile(":/qtwebchannel/qwebchannel.js")
    if f.open(QIODevice.OpenModeFlag.ReadOnly):
        data = bytes(f.readAll().data()).decode("utf-8", "replace")
        f.close()
        return data
    return ""


class AnmBrowserTab(QWidget):
    title_changed = Signal(str)

    def __init__(self, handler: AnmSchemeHandler, wallet: Wallet, parent=None):
        super().__init__(parent)
        self._current_name = ""
        # Ephemeral, isolated profile per tab (opaque origins, no cross-site storage bleed).
        self.profile = QWebEngineProfile(self)
        self.profile.installUrlSchemeHandler(ANM_SCHEME.encode(), handler)
        self.view = QWebEngineView(self)
        self.view.setPage(self._build_page(wallet))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.view)
        self.view.titleChanged.connect(self.title_changed)

    def _build_page(self, wallet: Wallet):
        from PySide6.QtWebEngineCore import QWebEnginePage
        page = QWebEnginePage(self.profile, self)
        channel, self._provider = bridge.make_channel(wallet, lambda: self._current_name, self)
        page.setWebChannel(channel)
        # Inject qwebchannel.js + the window.animica shim at document creation, main world.
        for name, src in (("qwebchannel", _qwebchannel_js()), ("animica-provider", bridge.PROVIDER_SHIM)):
            if not src:
                continue
            script = QWebEngineScript()
            script.setName(name)
            script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
            script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
            script.setRunsOnSubFrames(False)
            script.setSourceCode(src)
            page.scripts().insert(script)
        return page

    def navigate(self, raw: str) -> None:
        name = resolver.normalize_name(raw)
        if not name:
            return
        self._current_name = name
        try:
            rn = resolver.resolve(name)
        except resolver.ResolveError:
            # Let the scheme handler render the not-found card.
            self.view.load(QUrl(f"{ANM_SCHEME}://{name}/"))
            return
        if rn.content_cid:
            self.view.load(QUrl(f"{ANM_SCHEME}://{name}/"))
        elif rn.endpoint:
            # Endpoint-type .anm name → load its declared target (still entered via a .anm name).
            self.view.load(QUrl(rn.endpoint))
        else:
            self.view.load(QUrl(f"{ANM_SCHEME}://{name}/"))

    def current_name(self) -> str:
        return self._current_name


class MainWindow(QMainWindow):
    def __init__(self, wallet: Wallet | None = None):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1180, 760)
        self.wallet = wallet or Wallet()
        self.handler = AnmSchemeHandler(self)

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)

        self._build_toolbar()
        central = QWidget()
        v = QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self.tabs)
        self.setCentralWidget(central)

        self._build_side_actions()
        self.new_tab(HOME_NAME)

    # ---- chrome ----
    def _build_toolbar(self):
        tb = QToolBar()
        tb.setMovable(False)
        self.addToolBar(tb)
        for text, slot in (("←", self._back), ("→", self._forward), ("⟳", self._reload), ("⌂", self._home)):
            b = QPushButton(text)
            b.setFixedWidth(38)
            b.clicked.connect(slot)
            tb.addWidget(b)
        self.address = QLineEdit()
        self.address.setPlaceholderText("Enter a .anm name (e.g. develop.anm) — this browser is .anm-only")
        self.address.returnPressed.connect(self._go)
        tb.addWidget(self.address)
        go = QPushButton("Go")
        go.clicked.connect(self._go)
        tb.addWidget(go)
        nt = QPushButton("＋")
        nt.setFixedWidth(34)
        nt.clicked.connect(lambda: self.new_tab(HOME_NAME))
        tb.addWidget(nt)

    def _build_side_actions(self):
        menu = self.menuBar().addMenu("Animica Internet")
        for label, cb in (
            ("Directory / Search", lambda: DirectoryPanel(self.open_name, self).exec()),
            ("Reserve a name…", lambda: ReservePanel(self.wallet, self).exec()),
            ("Publish a site…", lambda: PublishPanel(self.wallet, self).exec()),
            ("Wallet…", lambda: WalletPanel(self.wallet, self).exec()),
        ):
            act = QAction(label, self)
            act.triggered.connect(cb)
            menu.addAction(act)

    # ---- tab helpers ----
    def new_tab(self, name: str = HOME_NAME):
        tab = AnmBrowserTab(self.handler, self.wallet, self)
        idx = self.tabs.addTab(tab, name)
        self.tabs.setCurrentIndex(idx)
        tab.title_changed.connect(lambda t, i=idx: self.tabs.setTabText(i, (t or name)[:22]))
        if name:
            tab.navigate(name)
            self.address.setText(f"{resolver.normalize_name(name)}.anm")
        return tab

    def _current(self) -> AnmBrowserTab | None:
        w = self.tabs.currentWidget()
        return w if isinstance(w, AnmBrowserTab) else None

    def _close_tab(self, idx: int):
        if self.tabs.count() > 1:
            self.tabs.removeTab(idx)

    def open_name(self, name: str):
        tab = self._current() or self.new_tab(name)
        tab.navigate(name)
        self.address.setText(f"{resolver.normalize_name(name)}.anm")

    def _go(self):
        self.open_name(self.address.text())

    def _home(self):
        self.open_name(HOME_NAME)

    def _back(self):
        t = self._current()
        if t:
            t.view.back()

    def _forward(self):
        t = self._current()
        if t:
            t.view.forward()

    def _reload(self):
        t = self._current()
        if t:
            t.view.reload()
