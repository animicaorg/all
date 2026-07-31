"""Wallet tab - send transactions and manage wallet."""

import json
import logging
import re
import os
import subprocess
import sys
from typing import Optional

from PySide6.QtCore import Qt, QRunnable, QThreadPool, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QMessageBox,
)

from animica_miner_gui.backend.config import MiningAppConfig
from animica_miner_gui.backend.node_controller import NodeController
from animica_miner_gui.backend.rpc_client import RPCClient

logger = logging.getLogger(__name__)

#: CSI/OSC escape sequences rich emits when it believes it has a terminal.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")

# Constants
MIN_ADDRESS_LENGTH = 42  # Minimum length for valid Animica address
TX_SEND_TIMEOUT = 60  # Timeout for transaction sending in seconds
ADDRESS_PREVIEW_LENGTH = 20  # Number of characters to show in address preview
ANM_BASE_UNITS = 1_000_000_000  # 1 ANM = 1e9 base units
WALLET_INFO_REFRESH_INTERVAL = 10000  # Refresh wallet info every 10 seconds (milliseconds)


class _WalletQueryTask(QRunnable):
    """Runs one wallet balance/nonce query off the GUI thread."""

    def __init__(self, tab: "WalletTab") -> None:
        super().__init__()
        self._tab = tab

    def run(self) -> None:  # pragma: no cover - thread body
        try:
            self._tab._query_wallet_info()
        except RuntimeError as exc:
            # The tab was destroyed while this query was in flight (app closing,
            # tab torn down). Qt raises "Signal source has been deleted" on the
            # emit. Expected during shutdown — not worth a traceback.
            logger.debug("wallet query abandoned: %s", exc)
        except Exception:
            logger.exception("wallet query failed")
        finally:
            try:
                self._tab._refresh_in_flight = False
            except RuntimeError:
                pass


class WalletTab(QWidget):
    """Wallet tab for sending transactions."""
    
    def __init__(self, config: MiningAppConfig, node_controller: NodeController, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.config = config
        self.node_controller = node_controller
        self.rpc_client: Optional[RPCClient] = None
        self._refresh_in_flight = False
        # Own the pool: its destructor waits for in-flight tasks, so a worker
        # can never reference this widget after Qt has deleted it.
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self.setup_ui()
        self.walletInfoReady.connect(self._apply_wallet_info)
        self.node_controller.rpcChanged.connect(self.on_rpc_changed)
        # Build a client straight from configuration rather than waiting for
        # NodeController to announce one. The tab is then useful on its own,
        # and it no longer depends on having been constructed before the
        # controller emits rpcChanged — an ordering assumption that left the
        # wallet permanently empty whenever it did not hold.
        try:
            configured_url = config.network.resolved_rpc_url()
            if configured_url:
                self.rpc_client = RPCClient(configured_url)
        except Exception:
            logger.exception("could not build an RPC client from configuration")
        self.setup_auto_refresh()
    
    def setup_ui(self) -> None:
        """Set up the UI."""
        layout = QVBoxLayout()
        
        # Wallet info group
        wallet_group = QGroupBox("Wallet Information")
        wallet_layout = QFormLayout()
        
        # Address with copy button
        address_layout = QHBoxLayout()
        self.address_label = QLabel(self.config.miner.payout_address or "Not configured")
        self.address_label.setWordWrap(True)
        self.address_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        address_layout.addWidget(self.address_label, stretch=1)
        
        self.copy_address_button = QPushButton("📋 Copy")
        self.copy_address_button.setMaximumWidth(80)
        self.copy_address_button.setToolTip("Copy address to clipboard")
        self.copy_address_button.clicked.connect(self.copy_address_to_clipboard)
        if not self.config.miner.payout_address:
            self.copy_address_button.setEnabled(False)
        address_layout.addWidget(self.copy_address_button)
        
        address_widget = QWidget()
        address_widget.setLayout(address_layout)
        wallet_layout.addRow("Address:", address_widget)
        
        # Balance with refresh button
        balance_layout = QHBoxLayout()
        self.balance_label = QLabel("--")
        self.balance_label.setStyleSheet("font-weight: bold; font-size: 12pt;")
        balance_layout.addWidget(self.balance_label, stretch=1)
        
        self.refresh_balance_button = QPushButton("🔄 Refresh")
        self.refresh_balance_button.setMaximumWidth(80)
        self.refresh_balance_button.setToolTip("Refresh wallet balance")
        self.refresh_balance_button.clicked.connect(self.refresh_wallet_info)
        if not self.config.miner.payout_address:
            self.refresh_balance_button.setEnabled(False)
        balance_layout.addWidget(self.refresh_balance_button)
        
        balance_widget = QWidget()
        balance_widget.setLayout(balance_layout)
        wallet_layout.addRow("Balance:", balance_widget)
        
        # Nonce (transaction count)
        self.nonce_label = QLabel("--")
        self.nonce_label.setToolTip("Current nonce - number of transactions sent from this address")
        wallet_layout.addRow("Nonce:", self.nonce_label)
        
        wallet_group.setLayout(wallet_layout)
        layout.addWidget(wallet_group)
        
        # Send transaction group
        send_group = QGroupBox("Send Transaction")
        send_layout = QFormLayout()
        
        # Recipient address
        self.recipient_input = QLineEdit()
        self.recipient_input.setPlaceholderText("anim1...")
        send_layout.addRow("To Address:", self.recipient_input)
        
        # Amount
        self.amount_input = QLineEdit()
        self.amount_input.setPlaceholderText("0.0")
        amount_help = QLabel("Amount in ANM (e.g., 1.5 for 1.5 ANM)")
        amount_help.setStyleSheet("color: gray; font-size: 11px;")
        send_layout.addRow("Amount:", self.amount_input)
        send_layout.addRow("", amount_help)
        
        # Send button
        button_layout = QHBoxLayout()
        self.send_button = QPushButton("Send Transaction")
        self.send_button.clicked.connect(self.send_transaction)
        self.send_button.setMinimumHeight(35)
        button_layout.addWidget(self.send_button)
        button_layout.addStretch()
        
        send_layout.addRow("", button_layout)
        
        send_group.setLayout(send_layout)
        layout.addWidget(send_group)
        
        # Status/result group
        result_group = QGroupBox("Transaction Result")
        result_layout = QVBoxLayout()
        
        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setMaximumHeight(150)
        result_layout.addWidget(self.result_text)
        
        result_group.setLayout(result_layout)
        layout.addWidget(result_group)
        
        layout.addStretch()
        self.setLayout(layout)
    
    def apply_config(self, config: MiningAppConfig) -> None:
        """Re-render everything that depends on configuration.

        The address label and the enabled state of Copy/Refresh were computed
        once in setup_ui(). On a first run the payout address is empty, so
        those buttons were created disabled and stayed disabled for the whole
        session — even after the user saved an address in the Configuration
        tab, which is exactly when they expect the Wallet tab to come alive.
        """
        self.config = config
        address = config.miner.payout_address or ""
        self.address_label.setText(address or "Not configured")
        self.copy_address_button.setEnabled(bool(address))
        self.refresh_balance_button.setEnabled(bool(address))
        if address:
            self.refresh_wallet_info()
        else:
            self.balance_label.setText("--")
            self.nonce_label.setText("--")

    def on_rpc_changed(self, rpc_url: str, token: str) -> None:
        """Update RPC client when node RPC changes."""
        try:
            self.rpc_client = RPCClient(rpc_url, token=token)
            if self.config.miner.payout_address:
                self.refresh_wallet_info()
        except Exception as e:
            logger.error(f"Failed to initialize RPC client: {e}")
    
    def setup_auto_refresh(self) -> None:
        """Set up timer to auto-refresh wallet info."""
        # Refresh wallet info periodically
        self.refresh_timer = QTimer(self)  # Set parent to ensure cleanup
        self.refresh_timer.timeout.connect(self.refresh_wallet_info)
        self.refresh_timer.start(WALLET_INFO_REFRESH_INTERVAL)
        
        # Deliberately no refresh here. Constructing the tab must not start
        # network work: it put a request in flight before the window was even
        # shown, and a worker outliving a short-lived widget (tests, tab
        # teardown) aborted the process. The timer fires shortly, and
        # on_rpc_changed / apply_config refresh on demand.
    
    def closeEvent(self, event) -> None:
        """Clean up resources when widget is closed."""
        # Stop accepting new queries and drain anything in flight before the
        # widget goes away.
        self._refresh_in_flight = True
        try:
            self._pool.waitForDone(3000)
        except Exception:
            pass
        # Stop the refresh timer to prevent memory leaks
        if hasattr(self, 'refresh_timer') and self.refresh_timer.isActive():
            self.refresh_timer.stop()
        super().closeEvent(event)
    
    #: Carries a finished wallet query back to the GUI thread.
    walletInfoReady = Signal(object, object, object)

    def refresh_wallet_info(self) -> None:
        """Query RPC for wallet balance and nonce, off the GUI thread.

        This used to make two blocking HTTP calls inline, on a 10s timer. When
        the endpoint was slow or half-open the whole window froze — measured at
        40s against an unreachable host — and the user had no idea why.
        """
        if not self.rpc_client:
            logger.info("wallet refresh skipped: no RPC endpoint yet")
            return
        if getattr(self, "_refresh_in_flight", False):
            return
        self._refresh_in_flight = True
        self._pool.start(_WalletQueryTask(self))

    def _query_wallet_info(self) -> None:
        """Body of the wallet query. Runs on a worker thread.

        Touches no widgets — results go back through walletInfoReady, which Qt
        marshals to the GUI thread.
        """
        if not self.rpc_client:
            return

        payout_address = self.config.miner.payout_address
        if not payout_address:
            self.walletInfoReady.emit("No payout address", "--", None)
            return

        try:
            balance_int = self.rpc_client.get_balance(payout_address)
            if balance_int is not None:
                balance_text = f"{balance_int / ANM_BASE_UNITS:.9f} ANM"
                balance_error = None
            else:
                balance_text = "Unable to query"
                balance_error = "node returned no balance"
        except Exception as e:
            # Was logger.debug under an INFO root, i.e. invisible. A wallet
            # stuck on "--" with nothing in the log the user is asked to send
            # is unsupportable.
            logger.warning("Failed to query balance for %s: %s", payout_address, e)
            balance_text = "Query failed"
            balance_error = str(e)

        try:
            nonce_int = self.rpc_client.get_nonce(payout_address)
            nonce_text = str(nonce_int) if nonce_int is not None else "Unable to query"
        except Exception as e:
            logger.warning("Failed to query nonce for %s: %s", payout_address, e)
            nonce_text = "Query failed"

        self.walletInfoReady.emit(balance_text, nonce_text, balance_error)

    @Slot(object, object, object)
    def _apply_wallet_info(self, balance_text, nonce_text, error) -> None:
        """Apply a finished wallet query. GUI thread only."""
        self._refresh_in_flight = False
        self.balance_label.setText(str(balance_text))
        self.nonce_label.setText(str(nonce_text))
        # Say why, instead of leaving an unexplained placeholder on screen.
        self.balance_label.setToolTip(str(error) if error else "")

    def copy_address_to_clipboard(self) -> None:
        """Copy the wallet address to clipboard."""
        address = self.config.miner.payout_address
        if not address:
            QMessageBox.warning(
                self,
                "No Address",
                "No payout address configured."
            )
            return
        
        try:
            clipboard = QApplication.clipboard()
            if clipboard:
                clipboard.setText(address)
                # Show brief notification with preview
                preview = address[:ADDRESS_PREVIEW_LENGTH] + "..." if len(address) > ADDRESS_PREVIEW_LENGTH else address
                QMessageBox.information(
                    self,
                    "Copied",
                    f"Address copied to clipboard!\n\n{preview}"
                )
            else:
                QMessageBox.warning(
                    self,
                    "Error",
                    "Unable to access clipboard."
                )
        except Exception as e:
            logger.error(f"Failed to copy address to clipboard: {e}")
            QMessageBox.warning(
                self,
                "Error",
                f"Failed to copy address: {str(e)}"
            )
    
    def send_transaction(self) -> None:
        """Send a transaction using the wallet CLI."""
        # Get sender address from config
        from_addr = self.config.miner.payout_address
        if not from_addr:
            QMessageBox.warning(
                self,
                "No Wallet",
                "No payout address configured. Please configure a wallet first."
            )
            return
        
        # Check if the address exists in wallets.json
        # Use configured wallet file path or default
        if self.config.miner.wallet_file:
            wallet_path = os.path.expanduser(self.config.miner.wallet_file)
        else:
            # Check environment variable, then default
            wallet_path = os.path.expanduser(
                os.environ.get("ANIMICA_WALLETS_FILE", "~/.animica/wallets.json")
            )
        address_in_wallet = False
        
        try:
            if os.path.exists(wallet_path):
                with open(wallet_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                
                # Handle both dict and list formats
                entries = data.get("wallets") if isinstance(data, dict) else data
                if isinstance(entries, list):
                    for w in entries:
                        if str(w.get("address")) == from_addr:
                            address_in_wallet = True
                            break
        except (json.JSONDecodeError, FileNotFoundError, PermissionError, KeyError) as e:
            logger.warning(f"Could not check wallet file: {e}")
        except Exception as e:
            # Catch any other unexpected errors but log them
            logger.error(f"Unexpected error checking wallet file: {e}", exc_info=True)
        
        if not address_in_wallet:
            reply = QMessageBox.warning(
                self,
                "Address Not in Wallet",
                f"The payout address:\n\n{from_addr}\n\n"
                f"is not found in your wallet file ({wallet_path}).\n\n"
                f"You cannot send transactions from addresses that aren't in your wallet file.\n\n"
                f"To fix this:\n"
                f"1. Go to Configuration and import/create a wallet with this address, OR\n"
                f"2. Change your payout address to one that exists in your wallet file",
                QMessageBox.StandardButton.Ok
            )
            return
        
        # Get recipient and amount
        to_addr = self.recipient_input.text().strip()
        amount_str = self.amount_input.text().strip()
        
        # Validate inputs
        if not to_addr:
            QMessageBox.warning(self, "Invalid Input", "Please enter a recipient address.")
            return
        
        if not to_addr.startswith("anim1") or len(to_addr) < MIN_ADDRESS_LENGTH:
            QMessageBox.warning(self, "Invalid Address", "Recipient address must be a valid Animica address (anim1...).")
            return
        
        if not amount_str:
            QMessageBox.warning(self, "Invalid Input", "Please enter an amount.")
            return
        
        try:
            amount = float(amount_str)
            if amount <= 0:
                QMessageBox.warning(self, "Invalid Amount", "Amount must be greater than 0.")
                return
        except ValueError:
            QMessageBox.warning(self, "Invalid Amount", "Please enter a valid number for amount.")
            return
        
        # Confirm transaction
        reply = QMessageBox.question(
            self,
            "Confirm Transaction",
            f"Send {amount} ANM to {to_addr[:20]}...?\n\nThis will use your configured wallet.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        # Disable send button during transaction
        self.send_button.setEnabled(False)
        self.result_text.setPlainText("Sending transaction...\n")
        
        try:
            # Build the tx send command
            # Use the animica CLI: animica tx send --from <addr> --to <addr> --value <amount>
            rpc_url = self.config.network.resolved_rpc_url()
            if not rpc_url:
                QMessageBox.warning(
                    self,
                    "RPC Not Ready",
                    "RPC is not ready yet. Start the local node to continue.",
                )
                return
            
            from animica_miner_gui.backend.cli_runner import (
                child_env,
                resolve_animica_cli,
            )

            cli = resolve_animica_cli()

            cmd = [
                *cli, "tx", "send",
                "--from", from_addr,
                "--to", to_addr,
                "--value", str(amount),
                "--rpc-url", rpc_url,
            ]

            self.result_text.append(f"Running: {' '.join(cmd)}\n")

            # Run the command
            result = subprocess.run(
                cmd,
                env=child_env(
                    {
                        # Keep rich from hard-wrapping the tx hash across lines
                        # and from emitting colour codes into captured output.
                        "COLUMNS": "200",
                        "NO_COLOR": "1",
                        "TERM": "dumb",
                    },
                    same_binary=(cli[0] == sys.executable),
                ),
                capture_output=True,
                text=True,
                timeout=TX_SEND_TIMEOUT
            )

            # A zero exit code alone is not proof the transaction happened —
            # require the CLI to have printed a transaction hash. Money moving
            # is not something to infer from an empty stdout.
            tx_hash = _extract_tx_hash(result.stdout)

            if result.returncode == 0 and tx_hash:
                self.result_text.append(f"✓ Transaction sent. Hash: {tx_hash}\n\n")
                self.result_text.append("Output:\n")
                self.result_text.append(result.stdout)

                # Clear inputs on success
                self.recipient_input.clear()
                self.amount_input.clear()

                # Refresh wallet info after successful transaction
                self.refresh_wallet_info()

                QMessageBox.information(
                    self,
                    "Success",
                    f"Transaction sent.\n\nHash: {tx_hash}"
                )
            elif result.returncode == 0:
                self.result_text.append(
                    "✗ The send command exited cleanly but returned no transaction "
                    "hash, so the transaction was NOT confirmed as sent.\n\n"
                )
                self.result_text.append(result.stdout or "(no output)")
                QMessageBox.warning(
                    self,
                    "Send Not Confirmed",
                    "The wallet command produced no transaction hash. Your funds "
                    "have NOT been sent. Check the result pane and your balance "
                    "before retrying.",
                )
            else:
                self.result_text.append("✗ Transaction failed!\n\n")
                self.result_text.append("Error:\n")
                self.result_text.append(result.stderr or result.stdout)
                
                QMessageBox.critical(
                    self,
                    "Transaction Failed",
                    f"Failed to send transaction. See result pane for details."
                )
        
        except subprocess.TimeoutExpired:
            self.result_text.append(f"✗ Transaction timed out after {TX_SEND_TIMEOUT} seconds.\n")
            QMessageBox.critical(self, "Timeout", "Transaction timed out.")
        
        except Exception as e:
            logger.error(f"Error sending transaction: {e}")
            self.result_text.append(f"✗ Error: {str(e)}\n")
            QMessageBox.critical(self, "Error", f"Failed to send transaction: {str(e)}")
        
        finally:
            self.send_button.setEnabled(True)


def _extract_tx_hash(stdout: str) -> str | None:
    """Pull a transaction hash out of `animica tx send` output.

    Used as positive proof that a send actually happened. A clean exit code is
    not enough: under PyInstaller a misresolved CLI can exit 0 having done
    nothing at all, and the wallet used to render that as a success.

    The CLI prints through `rich`, which hard-wraps to the console width and
    can split a 64-hex hash across lines::

        >>> Console(file=buf, width=60).print(f"Tx Hash: {h}")
        'Tx Hash: \n0xabababab...ababab\nababab\n'

    A naive ``\\b0x[0-9a-fA-F]{64}\\b`` never matches that, so a genuine send
    was reported as "your funds have NOT been sent" — far worse than the false
    success this check was added to prevent. Strip ANSI, then also search a
    whitespace-collapsed copy so a wrapped hash is still found.
    """
    if not stdout:
        return None

    text = _ANSI_RE.sub("", stdout)

    # "Tx Hash: 0x…" / "txid = 0x…" — allow the space in "Tx Hash".
    labelled = re.search(
        r"(?:tx[\s_-]*(?:id|hash)?|transaction(?:[\s_-]*hash)?)\s*[:=]\s*(0x[0-9a-fA-F]{64})",
        text,
        re.IGNORECASE,
    )
    if labelled:
        return labelled.group(1)

    bare = re.search(r"\b(0x[0-9a-fA-F]{64})\b", text)
    if bare:
        return bare.group(1)

    # Last resort: rich wrapped it. Rejoin and look again.
    unwrapped = re.sub(r"\s+", "", text)
    rewrapped = re.search(r"(0x[0-9a-fA-F]{64})", unwrapped)
    return rewrapped.group(1) if rewrapped else None
