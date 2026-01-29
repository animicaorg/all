#include "WalletWidget.h"
#include "WalletEngine.h"
#include "AccountsWidget.h"
#include "AddressBookWidget.h"
#include "CreateAccountDialog.h"
#include "UnlockDialog.h"
#include "BalanceTracker.h"
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QMessageBox>
#include <QTimer>
#include <QDateTime>

WalletWidget::WalletWidget(WalletEngine* engine, QWidget* parent)
    : QWidget(parent)
    , m_engine(engine)
{
    setupUi();
    
    // Connect to engine signals
    connect(m_engine, &WalletEngine::walletLocked, this, &WalletWidget::handleWalletLocked);
    connect(m_engine, &WalletEngine::walletUnlocked, this, &WalletWidget::handleWalletUnlocked);
    connect(m_engine, &WalletEngine::balanceUpdated, this, &WalletWidget::handleBalanceUpdated);
    connect(m_engine, &WalletEngine::syncStatusChanged, this, &WalletWidget::handleSyncStatusChanged);
    
    // Initial state
    updateToolbarState();
    updateStatus();
    
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
    
    m_unlockAction = m_toolbar->addAction("Unlock");
    m_unlockAction->setToolTip("Unlock wallet to perform operations");
    connect(m_unlockAction, &QAction::triggered, this, &WalletWidget::onUnlockAction);
    
    m_lockAction = m_toolbar->addAction("Lock");
    m_lockAction->setToolTip("Lock wallet and clear keys from memory");
    connect(m_lockAction, &QAction::triggered, this, &WalletWidget::onLockAction);
    
    m_toolbar->addSeparator();
    
    m_createAccountAction = m_toolbar->addAction("Create Account");
    m_createAccountAction->setToolTip("Create a new wallet account");
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
    
    layout->addWidget(m_tabWidget);
    
    // Status bar
    auto* statusBar = new QWidget(this);
    auto* statusLayout = new QHBoxLayout(statusBar);
    statusLayout->setContentsMargins(8, 4, 8, 4);
    
    m_statusLabel = new QLabel("Ready", this);
    m_balanceLabel = new QLabel("Total: 0.000000 ANM", this);
    m_syncLabel = new QLabel("", this);
    
    statusLayout->addWidget(m_statusLabel);
    statusLayout->addStretch();
    statusLayout->addWidget(m_balanceLabel);
    statusLayout->addWidget(m_syncLabel);
    
    statusBar->setStyleSheet("QWidget { background-color: #f5f5f5; border-top: 1px solid #d0d0d0; }");
    layout->addWidget(statusBar);
}

void WalletWidget::refresh()
{
    m_accountsWidget->refreshAccounts();
    m_addressBookWidget->refreshContacts();
    m_engine->refreshBalances();
    updateStatus();
}

void WalletWidget::updateToolbarState()
{
    bool locked = m_engine->isLocked();
    m_unlockAction->setEnabled(locked);
    m_lockAction->setEnabled(!locked);
    m_createAccountAction->setEnabled(!locked);
}

void WalletWidget::updateStatus()
{
    // Lock status
    if (m_engine->isLocked()) {
        m_statusLabel->setText("🔒 Locked");
    } else {
        int timeout = m_engine->autoLockTimeout();
        if (timeout > 0) {
            m_statusLabel->setText(QString("🔓 Unlocked (auto-lock: %1 min)").arg(timeout));
        } else {
            m_statusLabel->setText("🔓 Unlocked");
        }
    }
    
    // Total balance
    m_balanceLabel->setText(formatTotalBalance());
}

QString WalletWidget::formatTotalBalance() const
{
    if (m_engine->isLocked()) {
        return "Total: —";
    }
    
    quint64 total = 0;
    auto balances = m_engine->getBalances();
    for (const auto& balance : balances) {
        total += balance.confirmed;
    }
    
    double anm = total / 1e18;
    return QString("Total: %1 ANM").arg(anm, 0, 'f', 6);
}

void WalletWidget::onLockAction()
{
    m_engine->lockWallet();
}

void WalletWidget::onUnlockAction()
{
    emit unlockRequested();
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

void WalletWidget::handleCreateAccountRequested()
{
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
