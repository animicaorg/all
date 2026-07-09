#include "WalletWidget.h"

#include "AccountsWidget.h"
#include "AddressBookWidget.h"
#include "BalanceTracker.h"
#include "ContractInteractionWidget.h"
#include "CreateAccountDialog.h"
#include "ReceiveWidget.h"
#include "SendWidget.h"
#include "SettingsWidget.h"
#include "TransactionHistoryWidget.h"
#include "TransactionMonitor.h"
#include "WalletDatabase.h"
#include "WalletEngine.h"
#include "WalletSecuritySettings.h"
#include "../mining/MiningDashboardWidget.h"
#include "../rpc/AnimicaRpcClient.h"
#include "../rpc/RpcReply.h"
#include "../rpc/RpcSettings.h"

#include <QDateTime>
#include <QHBoxLayout>
#include <QInputDialog>
#include <QJsonDocument>
#include <QJsonObject>
#include <QMessageBox>
#include <QPushButton>
#include <QSettings>
#include <QTimer>
#include <QVBoxLayout>
#include <limits>

namespace {

bool isHostedRpcOutageMessage(const QString& message)
{
    return message.startsWith(QStringLiteral("Hosted RPC unavailable"), Qt::CaseInsensitive);
}

QString rpcFailureDetails(const QString& message)
{
    if (isHostedRpcOutageMessage(message)) {
        return QString(
            "%1\n\n"
            "The wallet reached https://rpc.animica.org/rpc, but the service behind it failed. "
            "This is a server-side outage; retry again shortly."
        ).arg(message);
    }

    return QString(
        "The wallet could not complete a request to https://rpc.animica.org/rpc.\n\n%1"
    ).arg(message);
}

bool tryParseChainIdString(const QString& text, int* chainIdOut)
{
    if (!chainIdOut) {
        return false;
    }
    QString normalized = text.trimmed();
    if (normalized.isEmpty()) {
        return false;
    }

    int base = 10;
    if (normalized.startsWith(QStringLiteral("0x"), Qt::CaseInsensitive)) {
        normalized = normalized.mid(2);
        base = 16;
    }

    bool ok = false;
    const qlonglong parsed = normalized.toLongLong(&ok, base);
    if (!ok || parsed < 0 || parsed > std::numeric_limits<int>::max()) {
        return false;
    }
    *chainIdOut = static_cast<int>(parsed);
    return true;
}

bool parseChainIdValue(const QJsonValue& value, int* chainIdOut)
{
    if (!chainIdOut) {
        return false;
    }

    if (value.isDouble()) {
        const double numeric = value.toDouble();
        if (numeric < 0 || numeric > std::numeric_limits<int>::max()) {
            return false;
        }
        *chainIdOut = static_cast<int>(numeric);
        return true;
    }

    if (value.isString()) {
        return tryParseChainIdString(value.toString(), chainIdOut);
    }

    if (value.isObject()) {
        const QJsonObject obj = value.toObject();
        if (parseChainIdValue(obj.value(QStringLiteral("chainId")), chainIdOut)) {
            return true;
        }
        if (parseChainIdValue(obj.value(QStringLiteral("chain_id")), chainIdOut)) {
            return true;
        }
    }

    return false;
}

} // namespace

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
    , m_retryConnectionAction(nullptr)
    , m_lockWalletAction(nullptr)
    , m_unlockWalletAction(nullptr)
{
    setupUi();

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

    m_engine->setExplorerUrl(QSettings().value("WalletQt/explorerUrl").toString());
    if (WalletSecuritySettings::walletEncryptionEnabled() && m_engine->isLoaded() && !m_engine->isLocked()) {
        m_engine->lockWallet();
    }
    updateToolbarState();
    updateStatus();

    auto* statusTimer = new QTimer(this);
    connect(statusTimer, &QTimer::timeout, this, &WalletWidget::updateStatus);
    statusTimer->start(5000);

    QTimer::singleShot(0, this, &WalletWidget::refresh);
}

void WalletWidget::setupUi()
{
    auto* layout = new QVBoxLayout(this);
    layout->setContentsMargins(0, 0, 0, 0);
    layout->setSpacing(0);

    m_toolbar = new QToolBar("Wallet Toolbar", this);
    m_toolbar->setMovable(false);

    m_unlockWalletAction = m_toolbar->addAction("Unlock Wallet");
    m_unlockWalletAction->setToolTip("Unlock the wallet store for account management");
    connect(m_unlockWalletAction, &QAction::triggered, this, &WalletWidget::onUnlockWalletAction);

    m_lockWalletAction = m_toolbar->addAction("Lock Wallet");
    m_lockWalletAction->setToolTip("Lock the wallet store for this session");
    connect(m_lockWalletAction, &QAction::triggered, this, &WalletWidget::onLockWalletAction);

    m_toolbar->addSeparator();

    m_createAccountAction = m_toolbar->addAction("Create Account");
    m_createAccountAction->setToolTip("Create a new wallet entry");
    connect(m_createAccountAction, &QAction::triggered, this, &WalletWidget::onCreateAccountAction);

    m_toolbar->addSeparator();

    m_refreshAction = m_toolbar->addAction("Refresh");
    m_refreshAction->setToolTip("Refresh balances and connection state");
    connect(m_refreshAction, &QAction::triggered, this, &WalletWidget::onRefreshAction);

    m_retryConnectionAction = m_toolbar->addAction("Retry RPC");
    m_retryConnectionAction->setToolTip("Retry the hosted Animica RPC connection");
    connect(m_retryConnectionAction, &QAction::triggered, this, &WalletWidget::retryRpcProbe);

    layout->addWidget(m_toolbar);

    m_connectionBanner = new QFrame(this);
    m_connectionBanner->setObjectName("connectionBanner");
    m_connectionBanner->setVisible(false);
    m_connectionBanner->setStyleSheet(
        "QFrame { background-color: #fff4e5; border-bottom: 1px solid #f59e0b; }"
        "QLabel#connectionBannerTitle { font-weight: 600; color: #9a3412; }"
        "QLabel#connectionBannerDetails { color: #7c2d12; }"
        "QPushButton { padding: 6px 12px; }"
    );
    auto* bannerLayout = new QHBoxLayout(m_connectionBanner);
    bannerLayout->setContentsMargins(16, 12, 16, 12);
    bannerLayout->setSpacing(12);
    auto* bannerTextLayout = new QVBoxLayout();
    bannerTextLayout->setContentsMargins(0, 0, 0, 0);
    bannerTextLayout->setSpacing(4);

    m_connectionBannerTitle = new QLabel(m_connectionBanner);
    m_connectionBannerTitle->setObjectName("connectionBannerTitle");
    bannerTextLayout->addWidget(m_connectionBannerTitle);

    m_connectionBannerDetails = new QLabel(m_connectionBanner);
    m_connectionBannerDetails->setObjectName("connectionBannerDetails");
    m_connectionBannerDetails->setWordWrap(true);
    bannerTextLayout->addWidget(m_connectionBannerDetails);

    bannerLayout->addLayout(bannerTextLayout, 1);

    auto* retryButton = new QPushButton("Retry", m_connectionBanner);
    connect(retryButton, &QPushButton::clicked, this, &WalletWidget::retryRpcProbe);
    bannerLayout->addWidget(retryButton);

    layout->addWidget(m_connectionBanner);

    m_tabWidget = new QTabWidget(this);

    m_miningWidget = new MiningDashboardWidget(m_engine, m_rpcClient, this);
    m_tabWidget->addTab(m_miningWidget, "Mining");

    m_accountsWidget = new AccountsWidget(m_engine, this);
    m_tabWidget->addTab(m_accountsWidget, "Accounts");
    connect(
        m_accountsWidget,
        &AccountsWidget::createAccountRequested,
        this,
        &WalletWidget::handleCreateAccountRequested
    );

    m_addressBookWidget = new AddressBookWidget(m_engine, this);
    m_tabWidget->addTab(m_addressBookWidget, "Address Book");

    m_sendWidget = new SendWidget(m_engine, m_rpcClient, m_database, m_monitor, this);
    m_tabWidget->addTab(m_sendWidget, "Send");

    m_receiveWidget = new ReceiveWidget(m_engine, this);
    m_tabWidget->addTab(m_receiveWidget, "Receive");

    m_historyWidget = new TransactionHistoryWidget(m_engine, this);
    m_tabWidget->addTab(m_historyWidget, "History");

    m_contractWidget = new ContractInteractionWidget(m_engine, this);
    m_tabWidget->addTab(m_contractWidget, "Contracts");

    m_settingsWidget = new SettingsWidget(m_engine->walletFilePath(), m_engine->dataDir(), this);
    m_tabWidget->addTab(m_settingsWidget, "Settings");
    connect(m_tabWidget, &QTabWidget::currentChanged, this, [this](int index) {
        QWidget* currentPage = m_tabWidget->widget(index);
        if (currentPage == m_historyWidget) {
            m_historyWidget->refresh();
        } else if (currentPage == m_receiveWidget) {
            m_receiveWidget->refresh();
        }
    });
    connect(
        m_settingsWidget,
        &SettingsWidget::settingsApplied,
        this,
        [this](const QString& explorerUrl, int pollIntervalMs, int timeoutMs) {
            if (m_rpcClient) {
                m_rpcClient->setEndpoint(RpcSettings::canonicalRpcUrl());
                m_rpcClient->setTimeout(timeoutMs);
            }
            m_engine->setRpcEndpoint(RpcSettings::canonicalRpcUrl());
            m_engine->setExplorerUrl(explorerUrl);
            if (m_engine->balanceTracker()) {
                m_engine->balanceTracker()->setPollingInterval(pollIntervalMs);
            }
            setRpcEndpoint(RpcSettings::canonicalRpcUrl());
            if (WalletSecuritySettings::walletEncryptionEnabled() && m_engine->isLoaded() && !m_engine->isLocked()) {
                m_engine->lockWallet();
            }
            refresh();
        }
    );

    layout->addWidget(m_tabWidget);

    auto* statusBar = new QWidget(this);
    auto* statusLayout = new QHBoxLayout(statusBar);
    statusLayout->setContentsMargins(8, 4, 8, 4);

    m_statusLabel = new QLabel("Ready", this);
    m_rpcStatusLabel = new QLabel("RPC: Connecting", this);
    m_rpcEndpointLabel = new QLabel("", this);
    m_rpcEndpointLabel->setStyleSheet("color: #666;");
    m_balanceLabel = new QLabel("Total: 0.000000 ANM", this);

    statusLayout->addWidget(m_statusLabel);
    statusLayout->addStretch();
    statusLayout->addWidget(m_rpcStatusLabel);
    statusLayout->addWidget(m_rpcEndpointLabel);
    statusLayout->addWidget(m_balanceLabel);

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
    probeRpcStatus();
    updateStatus();
}

void WalletWidget::updateToolbarState()
{
    const bool loaded = m_engine->isLoaded();
    const bool locked = loaded && m_engine->isLocked();
    const bool unlocked = loaded && !locked;

    m_createAccountAction->setEnabled(unlocked);
    m_lockWalletAction->setEnabled(unlocked);
    m_unlockWalletAction->setEnabled(locked);
    m_lockWalletAction->setVisible(unlocked);
    m_unlockWalletAction->setVisible(locked);
}

void WalletWidget::updateStatus()
{
    if (!m_engine->isLoaded()) {
        m_statusLabel->setText("Wallet store unavailable");
    } else if (m_engine->isLocked()) {
        m_statusLabel->setText("Wallet store locked");
    } else {
        m_statusLabel->setText(QString("Wallets: %1").arg(m_engine->listAccounts().size()));
    }

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
    const auto balances = m_engine->getBalances();
    for (const auto& balance : balances) {
        total += balance.confirmed;
    }

    const double anm = total / 1e9;
    return QString("Total: %1 ANM").arg(anm, 0, 'f', 6);
}

void WalletWidget::onCreateAccountAction()
{
    handleCreateAccountRequested();
}

void WalletWidget::onLockWalletAction()
{
    if (m_engine->isLoaded() && !m_engine->isLocked()) {
        m_engine->lockWallet();
    }
}

void WalletWidget::onUnlockWalletAction()
{
    if (!m_engine->isLoaded()) {
        const QString detail = m_engine->lastError().trimmed();
        QMessageBox::warning(
            this,
            "Wallet Unavailable",
            detail.isEmpty()
                ? QStringLiteral("The wallet store is unavailable.")
                : detail
        );
        return;
    }

    QString password;
    if (!requestWalletPassword(password)) {
        return;
    }

    if (m_engine->unlockWallet(password)) {
        return;
    }

    const QString detail = m_engine->lastError().trimmed();
    QMessageBox::warning(
        this,
        "Unlock Failed",
        detail.isEmpty()
            ? QStringLiteral("The wallet store could not be unlocked.")
            : QString("The wallet store could not be unlocked.\n\n%1").arg(detail)
    );
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
    Q_UNUSED(syncing);
}

void WalletWidget::handleRpcConnected()
{
    clearConnectionBanner();
    updateRpcStatusLabel("RPC: Connected", "#15803d");
}

void WalletWidget::handleRpcDisconnected()
{
    updateRpcStatusLabel("RPC: Unreachable", "#b91c1c");
    setConnectionBanner(
        "Cannot reach the Animica network.",
        "The wallet could not contact https://rpc.animica.org/rpc. Check internet access and retry."
    );
}

void WalletWidget::handleRpcError(const QString& message)
{
    m_lastRpcError = message;
    updateRpcStatusLabel("RPC: Error", "#b91c1c");
    setConnectionBanner(
        isHostedRpcOutageMessage(message)
            ? QStringLiteral("Hosted RPC temporarily unavailable.")
            : QStringLiteral("Cannot reach the Animica network."),
        rpcFailureDetails(message)
    );
}

void WalletWidget::retryRpcProbe()
{
    probeRpcStatus();
}

void WalletWidget::probeRpcStatus()
{
    if (!m_rpcClient) {
        setConnectionBanner(
            "RPC client unavailable.",
            "The wallet cannot create requests for the hosted Animica RPC endpoint."
        );
        return;
    }

    updateRpcStatusLabel("RPC: Checking", "#92400e");
    RpcReply* reply = m_rpcClient->getChainId();
    connect(reply, &RpcReply::finished, this, [this, reply]() {
        if (reply->error() != QNetworkReply::NoError) {
            // A 403 means the endpoint responded but gated the connectivity
            // probe. The node is reachable and sends work through the local
            // signer, so treat it as connected rather than a network outage.
            if (reply->httpStatusCode() == 403) {
                clearConnectionBanner();
                updateRpcStatusLabel("RPC: Connected", "#15803d");
                reply->deleteLater();
                return;
            }
            handleRpcError(reply->errorString());
            reply->deleteLater();
            return;
        }

        const QJsonDocument doc = QJsonDocument::fromJson(reply->readAll());
        if (!doc.isObject()) {
            setConnectionBanner(
                "Unexpected RPC response.",
                "The hosted endpoint returned malformed JSON while checking mainnet connectivity."
            );
            updateRpcStatusLabel("RPC: Error", "#b91c1c");
            reply->deleteLater();
            return;
        }

        const QJsonObject object = doc.object();
        const QJsonValue result = object.value("result");

        int chainId = 0;
        if (!parseChainIdValue(result, &chainId)) {
            setConnectionBanner(
                "Unexpected RPC response.",
                "The hosted endpoint did not return a valid chain ID."
            );
            updateRpcStatusLabel("RPC: Error", "#b91c1c");
            reply->deleteLater();
            return;
        }

        if (chainId != RpcSettings::canonicalChainId()) {
            setConnectionBanner(
                "Wrong network detected.",
                QString(
                    "The wallet expects Animica mainnet (chain ID %1) but the RPC returned chain ID %2."
                ).arg(RpcSettings::canonicalChainId()).arg(chainId)
            );
            updateRpcStatusLabel("RPC: Wrong Network", "#b91c1c");
            reply->deleteLater();
            return;
        }

        clearConnectionBanner();
        updateRpcStatusLabel("RPC: Connected", "#15803d");
        reply->deleteLater();
    });
}

void WalletWidget::setConnectionBanner(const QString& title, const QString& details)
{
    m_connectionBannerTitle->setText(title);
    m_connectionBannerDetails->setText(details);
    m_connectionBanner->setVisible(true);
}

void WalletWidget::clearConnectionBanner()
{
    m_lastRpcError.clear();
    m_connectionBanner->setVisible(false);
    m_connectionBannerTitle->clear();
    m_connectionBannerDetails->clear();
}

bool WalletWidget::requestWalletPassword(QString& password)
{
    password.clear();
    if (!WalletSecuritySettings::walletEncryptionEnabled()) {
        return true;
    }

    bool ok = false;
    const QString entered = QInputDialog::getText(
        this,
        "Wallet Password",
        "Enter wallet password to unlock:",
        QLineEdit::Password,
        QString(),
        &ok
    );
    if (!ok) {
        return false;
    }
    if (!WalletSecuritySettings::verifyTransferPassword(entered)) {
        QMessageBox::warning(
            this,
            "Unlock Failed",
            "Wallet password is incorrect."
        );
        return false;
    }

    password = entered;
    return true;
}

void WalletWidget::handleCreateAccountRequested()
{
    if (!m_engine->isLoaded()) {
        QMessageBox::warning(
            this,
            "Wallet Unavailable",
            m_engine->lastError().trimmed().isEmpty()
                ? QStringLiteral("The wallet store is unavailable. The application could not open or create wallets.json.")
                : m_engine->lastError().trimmed()
        );
        return;
    }

    if (m_engine->isLocked()) {
        QString password;
        if (!requestWalletPassword(password)) {
            return;
        }
        if (!m_engine->unlockWallet(password)) {
            const QString detail = m_engine->lastError().trimmed();
            QMessageBox::warning(
                this,
                "Unlock Failed",
                detail.isEmpty()
                    ? QStringLiteral("The wallet store could not be unlocked for account creation.")
                    : QString("The wallet store could not be unlocked for account creation.\n\n%1").arg(detail)
            );
            return;
        }
    }

    CreateAccountDialog dialog(m_engine, this);
    if (dialog.exec() != QDialog::Accepted) {
        return;
    }

    m_accountsWidget->refreshAccounts();
    const QString address = dialog.generatedAddress();
    if (!address.isEmpty()) {
        QMessageBox::information(this, "Success", QString("Account created.\n\nAddress:\n%1").arg(address));
    }
}

void WalletWidget::updateRpcStatusLabel(const QString& status, const QString& color)
{
    m_rpcStatusLabel->setText(status);
    m_rpcStatusLabel->setStyleSheet(QString("color: %1;").arg(color));
}
