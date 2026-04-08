#include "WalletWidget.h"
#include "WalletEngine.h"
#include "AccountsWidget.h"
#include "AddressBookWidget.h"
#include "CreateAccountDialog.h"
#include "SendWidget.h"
#include "ReceiveWidget.h"
#include "TransactionHistoryWidget.h"
#include "ContractInteractionWidget.h"
#include "SettingsWidget.h"
#include "BalanceTracker.h"
#include "../rpc/AnimicaRpcClient.h"
#include "../rpc/RpcSettings.h"
#include "WalletDatabase.h"
#include "TransactionMonitor.h"
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QMessageBox>
#include <QSettings>
#include <QTimer>
#include <QDateTime>

WalletWidget::WalletWidget(
    WalletEngine* engine,
    AnimicaRpcClient* rpcClient,
    WalletDatabase* database,
    TransactionMonitor* monitor,
    QWidget* parent
)
    : QWidget(parent)
    , m_engine(engine)
    , m_rpcClient(rpcClient)
    , m_database(database)
    , m_monitor(monitor)
{
    setupUi();
    
    // Connect to engine signals
    connect(m_engine, &WalletEngine::walletLocked, this, &WalletWidget::handleWalletLocked);
    connect(m_engine, &WalletEngine::walletUnlocked, this, &WalletWidget::handleWalletUnlocked);
    connect(m_engine, &WalletEngine::balanceUpdated, this, &WalletWidget::handleBalanceUpdated);
    connect(m_engine, &WalletEngine::syncStatusChanged, this, &WalletWidget::handleSyncStatusChanged);

    if (m_rpcClient) {
        connect(m_rpcClient, &AnimicaRpcClient::connected, this, &WalletWidget::handleRpcConnected);
        connect(m_rpcClient, &AnimicaRpcClient::disconnected, this, &WalletWidget::handleRpcDisconnected);
        connect(m_rpcClient, &AnimicaRpcClient::error, this, &WalletWidget::handleRpcError);
        setRpcEndpoint(m_rpcClient->endpoint());
    }
    
    // Initial state
    m_engine->setExplorerUrl(QSettings().value("WalletQt/explorerUrl").toString());
    updateToolbarState();
    refresh();
    
    // Periodic status updates
    auto* statusTimer = new QTimer(this);
    connect(statusTimer, &QTimer::timeout, this, &WalletWidget::updateStatus);
    statusTimer->start(5000);  // Update every 5 seconds
}

void WalletWidget::setupUi()
{
    auto* layout = new QVBoxLayout(this);
    layout->setContentsMargins(0, 0, 0, 0);
    layout->setSpacing(0);
    
    // Toolbar
    m_toolbar = new QToolBar("Wallet Toolbar", this);
    m_toolbar->setMovable(false);
    
    m_createAccountAction = m_toolbar->addAction("Create Account");
    m_createAccountAction->setToolTip("Create a new wallet entry");
    connect(m_createAccountAction, &QAction::triggered, this, &WalletWidget::onCreateAccountAction);
    
    m_toolbar->addSeparator();
    
    m_refreshAction = m_toolbar->addAction("Refresh");
    m_refreshAction->setToolTip("Refresh balances and sync status");
    connect(m_refreshAction, &QAction::triggered, this, &WalletWidget::onRefreshAction);
    
    layout->addWidget(m_toolbar);
    
    // Tab widget for different views
    m_tabWidget = new QTabWidget(this);
    
    // Accounts tab
    m_accountsWidget = new AccountsWidget(m_engine, this);
    m_tabWidget->addTab(m_accountsWidget, "Accounts");
    connect(m_accountsWidget, &AccountsWidget::createAccountRequested,
            this, &WalletWidget::handleCreateAccountRequested);
    
    // Address Book tab
    m_addressBookWidget = new AddressBookWidget(m_engine, this);
    m_tabWidget->addTab(m_addressBookWidget, "Address Book");

    // Send tab
    m_sendWidget = new SendWidget(m_engine, m_rpcClient, m_database, m_monitor, this);
    m_tabWidget->addTab(m_sendWidget, "Send");

    // Receive tab
    m_receiveWidget = new ReceiveWidget(m_engine, this);
    m_tabWidget->addTab(m_receiveWidget, "Receive");

    // History tab
    m_historyWidget = new TransactionHistoryWidget(m_engine, this);
    m_tabWidget->addTab(m_historyWidget, "History");

    // Contracts tab
    m_contractWidget = new ContractInteractionWidget(m_engine, this);
    m_tabWidget->addTab(m_contractWidget, "Contracts");

    // Settings tab
    m_settingsWidget = new SettingsWidget(m_engine->walletFilePath(), m_engine->dataDir(), this);
    m_tabWidget->addTab(m_settingsWidget, "Settings");
    connect(m_settingsWidget, &SettingsWidget::rpcSettingsApplied,
            this,
            [this](const RpcEndpointSettings& settings, const QString& explorerUrl, int pollIntervalMs, int timeoutMs) {
                if (m_rpcClient) {
                    m_rpcClient->setEndpoint(RpcSettings::toUrl(settings).toString());
                    m_rpcClient->setTimeout(timeoutMs);
                }
                m_engine->setRpcEndpoint(RpcSettings::toUrl(settings).toString());
                m_engine->setExplorerUrl(explorerUrl);
                if (m_engine->balanceTracker()) {
                    m_engine->balanceTracker()->setPollingInterval(pollIntervalMs);
                }
                setRpcEndpoint(RpcSettings::toDisplayUrl(settings));
                refresh();
            });
    
    layout->addWidget(m_tabWidget);
    
    // Status bar
    auto* statusBar = new QWidget(this);
    auto* statusLayout = new QHBoxLayout(statusBar);
    statusLayout->setContentsMargins(8, 4, 8, 4);
    
    m_statusLabel = new QLabel("Ready", this);
    m_rpcStatusLabel = new QLabel("RPC: Disconnected", this);
    m_rpcEndpointLabel = new QLabel("", this);
    m_rpcEndpointLabel->setStyleSheet("color: #666;");
    m_balanceLabel = new QLabel("Total: 0.000000 ANM", this);
    m_syncLabel = new QLabel("", this);
    
    statusLayout->addWidget(m_statusLabel);
    statusLayout->addStretch();
    statusLayout->addWidget(m_rpcStatusLabel);
    statusLayout->addWidget(m_rpcEndpointLabel);
    statusLayout->addWidget(m_balanceLabel);
    statusLayout->addWidget(m_syncLabel);
    
    statusBar->setStyleSheet("QWidget { background-color: #f5f5f5; border-top: 1px solid #d0d0d0; }");
    layout->addWidget(statusBar);
}

void WalletWidget::refresh()
{
    m_accountsWidget->refreshAccounts();
    m_addressBookWidget->refreshContacts();
    m_receiveWidget->refresh();
    if (m_historyWidget) {
        m_historyWidget->refresh();
    }
    m_engine->refreshBalances();
    updateStatus();
}

void WalletWidget::updateToolbarState()
{
    const bool createEnabled = m_engine->isLoaded() && !m_engine->isLocked();
    m_createAccountAction->setEnabled(createEnabled);
}

void WalletWidget::updateStatus()
{
    // Lock status
    if (!m_engine->isLoaded()) {
        m_statusLabel->setText("Wallet store unavailable");
    } else if (m_engine->isLocked()) {
        m_statusLabel->setText("Wallet store locked");
    } else {
        m_statusLabel->setText(QString("Wallets: %1").arg(m_engine->listAccounts().size()));
    }
    
    // Total balance
    m_balanceLabel->setText(formatTotalBalance());
}

void WalletWidget::setRpcEndpoint(const QString& endpoint)
{
    m_rpcEndpointLabel->setText(QString("Endpoint: %1").arg(endpoint));
    m_engine->setRpcEndpoint(endpoint);
}

QString WalletWidget::formatTotalBalance() const
{
    if (!m_engine->isLoaded() || m_engine->isLocked()) {
        return "Total: —";
    }
    
    quint64 total = 0;
    auto balances = m_engine->getBalances();
    for (const auto& balance : balances) {
        total += balance.confirmed;
    }
    
    double anm = total / 1e9;
    return QString("Total: %1 ANM").arg(anm, 0, 'f', 6);
}

void WalletWidget::onCreateAccountAction()
{
    handleCreateAccountRequested();
}

void WalletWidget::onRefreshAction()
{
    refresh();
}

void WalletWidget::handleWalletLocked()
{
    updateToolbarState();
    updateStatus();
    m_accountsWidget->refreshAccounts();
}

void WalletWidget::handleWalletUnlocked()
{
    updateToolbarState();
    updateStatus();
    refresh();
}

void WalletWidget::handleBalanceUpdated(const QString& address, const Balance& balance)
{
    Q_UNUSED(address);
    Q_UNUSED(balance);
    updateStatus();
}

void WalletWidget::handleSyncStatusChanged(bool syncing)
{
    if (syncing) {
        m_syncLabel->setText("⟳ Syncing...");
    } else {
        m_syncLabel->setText("✓ Synced");
        // Clear after a moment
        QTimer::singleShot(3000, this, [this]() {
            m_syncLabel->setText("");
        });
    }
}

void WalletWidget::handleRpcConnected()
{
    updateRpcStatusLabel("RPC: Connected", "#15803d");
}

void WalletWidget::handleRpcDisconnected()
{
    updateRpcStatusLabel("RPC: Disconnected", "#b91c1c");
}

void WalletWidget::handleRpcError(const QString& message)
{
    Q_UNUSED(message);
    updateRpcStatusLabel("RPC: Error", "#b91c1c");
}

void WalletWidget::handleCreateAccountRequested()
{
    if (!m_engine->isLoaded()) {
        QMessageBox::warning(this, "Wallet Unavailable",
                             "The wallet store is unavailable. The application could not open or create wallets.json.");
        return;
    }

    if (m_engine->isLocked()) {
        QMessageBox::information(this, "Wallet Locked",
                                "Please unlock the wallet first to create an account.");
        return;
    }
    
    CreateAccountDialog dialog(m_engine, this);
    if (dialog.exec() == QDialog::Accepted) {
        // Account created successfully
        m_accountsWidget->refreshAccounts();
        
        QString addr = dialog.generatedAddress();
        if (!addr.isEmpty()) {
            QMessageBox::information(this, "Success",
                                    QString("Account created!\n\nAddress:\n%1").arg(addr));
        }
    }
}

void WalletWidget::updateRpcStatusLabel(const QString& status, const QString& color)
{
    m_rpcStatusLabel->setText(status);
    m_rpcStatusLabel->setStyleSheet(QString("color: %1;").arg(color));
}
