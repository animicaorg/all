#include "L2Widget.h"

#include "WalletAccount.h"
#include "WalletEngine.h"
#include "../rpc/AnimicaRpcClient.h"

#include <QFormLayout>
#include <QFrame>
#include <QGroupBox>
#include <QHBoxLayout>
#include <QMessageBox>
#include <QSignalBlocker>
#include <QVBoxLayout>
#include <QtConcurrent/QtConcurrent>

namespace {

constexpr double kBaseUnitsPerAnmDouble = 1e9;
constexpr int kPollIntervalMs = 2000;
constexpr int kMaxPollAttempts = 60; // ~2 minutes toward PROVEN

const char* kPrimaryButtonStyle =
    "QPushButton {"
    "  background-color: #1976D2;"
    "  color: white;"
    "  border: none;"
    "  border-radius: 4px;"
    "  padding: 8px 16px;"
    "  font-weight: bold;"
    "}"
    "QPushButton:hover { background-color: #1565C0; }"
    "QPushButton:disabled { background-color: #90A4AE; }";

// Lifecycle stages the sequencer reports, ordered non-terminal -> terminal.
// SOFT_CONFIRMED is sequencer acceptance, NOT settlement — it must never be
// presented as L1 finality.
bool isProven(const QString& status)
{
    return status == "PROVEN" || status == "L1_SUBMITTED" || status == "L1_FINALIZED";
}

bool isFailure(const QString& status)
{
    return status == "FAILED" || status == "REVERTED";
}

} // namespace

L2Widget::L2Widget(WalletEngine* engine, AnimicaRpcClient* rpcClient, QWidget* parent)
    : QWidget(parent)
    , m_engine(engine)
    , m_rpcClient(rpcClient)
    , m_accountCombo(nullptr)
    , m_l2BalanceLabel(nullptr)
    , m_statusEnabledLabel(nullptr)
    , m_statusModeLabel(nullptr)
    , m_statusChainLabel(nullptr)
    , m_statusSettlementLabel(nullptr)
    , m_statusHeadBatchLabel(nullptr)
    , m_statusStateRootLabel(nullptr)
    , m_statusBackendLabel(nullptr)
    , m_depositAddressLabel(nullptr)
    , m_refreshButton(nullptr)
    , m_recipientEdit(nullptr)
    , m_amountSpinBox(nullptr)
    , m_sendButton(nullptr)
    , m_sendStatusView(nullptr)
    , m_sendWatcher(new QFutureWatcher<QJsonObject>(this))
    , m_pollTimer(new QTimer(this))
    , m_pollAttempts(0)
{
    setupUi();

    connect(m_sendWatcher, &QFutureWatcher<QJsonObject>::finished, this, &L2Widget::handleSendFinished);

    m_pollTimer->setInterval(kPollIntervalMs);
    connect(m_pollTimer, &QTimer::timeout, this, &L2Widget::pollTransactionStatus);
}

L2Widget::~L2Widget()
{
    if (m_sendWatcher->isRunning()) {
        m_sendWatcher->future().waitForFinished();
    }
}

void L2Widget::setupUi()
{
    auto* root = new QVBoxLayout(this);

    auto* intro = new QLabel(
        "ANM Instant is Animica's native L2. It is the SAME asset as ANM (L1) "
        "shown as a distinct, fast balance. Fund it by sending L1 ANM to the "
        "bridge deposit address below; withdraw back to L1 from the CLI/SDK.",
        this);
    intro->setWordWrap(true);
    root->addWidget(intro);

    // ---- Account + balance ---------------------------------------------
    auto* accountRow = new QHBoxLayout();
    accountRow->addWidget(new QLabel("Account:", this));
    m_accountCombo = new QComboBox(this);
    m_accountCombo->setMinimumWidth(320);
    accountRow->addWidget(m_accountCombo);
    accountRow->addStretch();
    m_refreshButton = new QPushButton("Refresh", this);
    accountRow->addWidget(m_refreshButton);
    root->addLayout(accountRow);

    m_l2BalanceLabel = new QLabel("ANM Instant balance: —", this);
    QFont balanceFont = m_l2BalanceLabel->font();
    balanceFont.setPointSizeF(balanceFont.pointSizeF() + 2.0);
    balanceFont.setBold(true);
    m_l2BalanceLabel->setFont(balanceFont);
    root->addWidget(m_l2BalanceLabel);

    // ---- L2 status panel ------------------------------------------------
    auto* statusBox = new QGroupBox("L2 status", this);
    auto* statusForm = new QFormLayout(statusBox);
    m_statusEnabledLabel = new QLabel("—", statusBox);
    m_statusModeLabel = new QLabel("—", statusBox);
    m_statusChainLabel = new QLabel("—", statusBox);
    m_statusSettlementLabel = new QLabel("—", statusBox);
    m_statusHeadBatchLabel = new QLabel("—", statusBox);
    m_statusStateRootLabel = new QLabel("—", statusBox);
    m_statusBackendLabel = new QLabel("—", statusBox);
    m_statusStateRootLabel->setTextInteractionFlags(Qt::TextSelectableByMouse);
    statusForm->addRow("Enabled:", m_statusEnabledLabel);
    statusForm->addRow("Mode:", m_statusModeLabel);
    statusForm->addRow("L2 chain id:", m_statusChainLabel);
    statusForm->addRow("Settlement:", m_statusSettlementLabel);
    statusForm->addRow("Head batch:", m_statusHeadBatchLabel);
    statusForm->addRow("State root:", m_statusStateRootLabel);
    statusForm->addRow("Sig backend:", m_statusBackendLabel);
    root->addWidget(statusBox);

    // ---- Deposit (L1 -> L2) --------------------------------------------
    auto* depositBox = new QGroupBox("Deposit ANM to L2", this);
    auto* depositLayout = new QVBoxLayout(depositBox);
    auto* depositHelp = new QLabel(
        "Send an ordinary L1 transfer to the bridge deposit address; the node "
        "credits your L2 balance after L1 finality.",
        depositBox);
    depositHelp->setWordWrap(true);
    depositLayout->addWidget(depositHelp);
    m_depositAddressLabel = new QLabel("Bridge deposit address: —", depositBox);
    m_depositAddressLabel->setTextInteractionFlags(Qt::TextSelectableByMouse);
    QFont mono("monospace");
    mono.setStyleHint(QFont::TypeWriter);
    m_depositAddressLabel->setFont(mono);
    depositLayout->addWidget(m_depositAddressLabel);
    root->addWidget(depositBox);

    // ---- Send Instant ---------------------------------------------------
    auto* sendBox = new QGroupBox("Send ANM Instant (L2)", this);
    auto* sendForm = new QFormLayout(sendBox);
    m_recipientEdit = new QLineEdit(sendBox);
    m_recipientEdit->setPlaceholderText("anim1... or 0x-hex 32-byte address");
    sendForm->addRow("Recipient:", m_recipientEdit);

    m_amountSpinBox = new QDoubleSpinBox(sendBox);
    m_amountSpinBox->setDecimals(9);
    m_amountSpinBox->setMinimum(0.0);
    m_amountSpinBox->setMaximum(1000000000.0);
    m_amountSpinBox->setSingleStep(0.1);
    m_amountSpinBox->setSuffix(" ANM");
    sendForm->addRow("Amount:", m_amountSpinBox);

    m_sendButton = new QPushButton("Send Instant", sendBox);
    m_sendButton->setStyleSheet(kPrimaryButtonStyle);
    sendForm->addRow(QString(), m_sendButton);
    root->addWidget(sendBox);

    m_sendStatusView = new QTextEdit(this);
    m_sendStatusView->setReadOnly(true);
    m_sendStatusView->setMaximumHeight(140);
    root->addWidget(m_sendStatusView);

    root->addStretch();

    connect(m_accountCombo, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, &L2Widget::onAccountChanged);
    connect(m_refreshButton, &QPushButton::clicked, this, &L2Widget::refresh);
    connect(m_sendButton, &QPushButton::clicked, this, &L2Widget::onSendClicked);
}

void L2Widget::showEvent(QShowEvent* event)
{
    QWidget::showEvent(event);
    refresh();
}

void L2Widget::refresh()
{
    updateAccounts();
    loadL2Info();
}

void L2Widget::updateAccounts()
{
    if (!m_engine) {
        return;
    }
    const QString previous = m_accountCombo ? m_accountCombo->currentData().toString() : QString();

    QSignalBlocker blocker(m_accountCombo);
    m_accountCombo->clear();
    const auto accounts = m_engine->listAccounts();
    int selectIndex = -1;
    for (const WalletAccount& account : accounts) {
        if (account.address.isEmpty()) {
            continue;
        }
        const QString label = account.isDefault
            ? QString("%1 (default)").arg(account.label.isEmpty() ? account.address : account.label)
            : (account.label.isEmpty() ? account.address : account.label);
        m_accountCombo->addItem(QString("%1 | %2").arg(label, account.address), account.address);
        if (!previous.isEmpty() && account.address == previous) {
            selectIndex = m_accountCombo->count() - 1;
        } else if (selectIndex < 0 && account.isDefault) {
            selectIndex = m_accountCombo->count() - 1;
        }
    }
    if (selectIndex >= 0) {
        m_accountCombo->setCurrentIndex(selectIndex);
    }
    m_sendButton->setEnabled(m_engine && !m_engine->isLocked() && m_accountCombo->count() > 0);
}

QString L2Widget::currentAccountAddress() const
{
    return m_accountCombo ? m_accountCombo->currentData().toString() : QString();
}

void L2Widget::onAccountChanged(int)
{
    loadL2Info();
}

void L2Widget::loadL2Info()
{
    if (!m_engine) {
        return;
    }
    // Reads over the shared RPC client (main-thread QNAM) — synchronous.
    applyStatus(m_engine->l2Status());

    const QString address = currentAccountAddress();
    if (address.isEmpty()) {
        m_l2BalanceLabel->setText("ANM Instant balance: —");
        return;
    }
    applyBalance(m_engine->l2Balance(address));
}

void L2Widget::applyStatus(const QJsonObject& status)
{
    if (status.isEmpty()) {
        m_statusEnabledLabel->setText("unavailable (node offline or L2 disabled)");
        m_statusModeLabel->setText("—");
        m_statusChainLabel->setText("—");
        m_statusSettlementLabel->setText("—");
        m_statusHeadBatchLabel->setText("—");
        m_statusStateRootLabel->setText("—");
        m_statusBackendLabel->setText("—");
        m_depositAddressLabel->setText("Bridge deposit address: —");
        return;
    }

    m_statusEnabledLabel->setText(status.value("enabled").toBool() ? "yes" : "no");
    m_statusModeLabel->setText(status.value("mode").toString("—"));
    m_statusChainLabel->setText(QString::number(status.value("l2ChainId").toVariant().toLongLong()));
    m_statusSettlementLabel->setText(status.value("settlementMode").toString("—"));
    m_statusHeadBatchLabel->setText(QString::number(status.value("headBatch").toVariant().toLongLong()));
    m_statusStateRootLabel->setText(status.value("stateRoot").toString("—"));
    m_statusBackendLabel->setText(status.value("sigBackend").toString("—"));

    // Deposit address: read defensively from the bridge summary if the node
    // exposes it; otherwise guide the user to the node's bridge config.
    const QJsonObject bridge = status.value("bridge").toObject();
    QString deposit;
    for (const char* key : {"depositAddress", "address", "bridgeAddress", "l1Address"}) {
        const QString v = bridge.value(QString::fromLatin1(key)).toString();
        if (!v.isEmpty()) {
            deposit = v;
            break;
        }
    }
    if (deposit.isEmpty()) {
        deposit = status.value("bridgeAddress").toString();
    }
    m_depositAddressLabel->setText(
        deposit.isEmpty()
            ? QString("Bridge deposit address: not published by node (see the node's L2 bridge config)")
            : QString("Bridge deposit address: %1").arg(deposit));
}

void L2Widget::applyBalance(const QJsonObject& balance)
{
    if (balance.isEmpty()) {
        m_l2BalanceLabel->setText("ANM Instant balance: unavailable");
        return;
    }
    const QString nanos = balance.value("balance").toVariant().toString();
    m_l2BalanceLabel->setText(QString("ANM Instant balance: %1 ANM").arg(formatNanosAsAnm(nanos)));
}

QString L2Widget::formatNanosAsAnm(const QString& nanos)
{
    bool ok = false;
    const qint64 value = nanos.toLongLong(&ok);
    if (!ok) {
        return "0.000000000";
    }
    return QString::number(static_cast<double>(value) / kBaseUnitsPerAnmDouble, 'f', 9);
}

void L2Widget::appendStatus(const QString& line)
{
    m_sendStatusView->append(line);
}

void L2Widget::onSendClicked()
{
    if (!m_engine || m_sendWatcher->isRunning()) {
        return;
    }
    if (m_engine->isLocked()) {
        QMessageBox::warning(this, "Wallet locked", "Unlock the wallet before sending.");
        return;
    }
    const QString fromAddress = currentAccountAddress();
    const QString toAddress = m_recipientEdit->text().trimmed();
    const double amount = m_amountSpinBox->value();

    if (fromAddress.isEmpty()) {
        QMessageBox::warning(this, "No account", "Select a sending account.");
        return;
    }
    if (toAddress.isEmpty()) {
        QMessageBox::warning(this, "Recipient required", "Enter a recipient address.");
        return;
    }
    if (amount <= 0.0) {
        QMessageBox::warning(this, "Amount required", "Enter an amount greater than zero.");
        return;
    }

    const QString amountText = QString::number(amount, 'f', 9);
    const QString confirmation = QString(
        "Send %1 ANM on ANM Instant (L2)\n\nfrom\n%2\n\nto\n%3\n\n"
        "This signs with your ML-DSA-65 key and submits to the L2 sequencer. "
        "L1 ANM is not touched.")
        .arg(amountText, fromAddress, toAddress);
    if (QMessageBox::question(this, "Confirm ANM Instant transfer", confirmation,
                              QMessageBox::Yes | QMessageBox::No) != QMessageBox::Yes) {
        return;
    }

    m_sendButton->setEnabled(false);
    m_pollTimer->stop();
    m_pendingTxid.clear();
    m_sendStatusView->clear();
    appendStatus(QString("Preparing + signing + submitting %1 ANM to %2 ...").arg(amountText, toAddress));

    WalletEngine* engine = m_engine;
    m_sendWatcher->setFuture(QtConcurrent::run([engine, fromAddress, toAddress, amountText]() {
        return engine->sendInstant(fromAddress, toAddress, amountText);
    }));
}

void L2Widget::handleSendFinished()
{
    const QJsonObject result = m_sendWatcher->future().result();
    m_sendButton->setEnabled(m_engine && !m_engine->isLocked() && m_accountCombo->count() > 0);

    if (result.isEmpty()) {
        const QString err = m_engine ? m_engine->lastError() : QString();
        appendStatus(QString("Submit failed%1")
                         .arg(err.isEmpty() ? QString(".") : QString(": %1").arg(err)));
        return;
    }

    const QString txid = result.value("txid").toString();
    const QString fee = result.value("fee").toVariant().toString();
    appendStatus(QString("Submitted. txid=%1").arg(txid));
    if (!fee.isEmpty()) {
        appendStatus(QString("L2 fee: %1 nanos").arg(fee));
    }
    appendStatus("Waiting for PROVEN (sequencer acceptance is NOT settlement)...");

    m_pendingTxid = txid;
    m_pollAttempts = 0;
    if (!m_pendingTxid.isEmpty()) {
        m_pollTimer->start();
    }

    // Optimistically refresh the balance view.
    const QString address = currentAccountAddress();
    if (!address.isEmpty()) {
        applyBalance(m_engine->l2Balance(address));
    }
}

void L2Widget::pollTransactionStatus()
{
    if (m_pendingTxid.isEmpty() || !m_engine) {
        m_pollTimer->stop();
        return;
    }
    if (++m_pollAttempts > kMaxPollAttempts) {
        m_pollTimer->stop();
        appendStatus("Still not PROVEN after polling window; check status later.");
        return;
    }

    const QJsonObject tx = m_engine->l2TransactionStatus(m_pendingTxid);
    if (tx.isEmpty()) {
        return; // transient; keep polling
    }
    const QString status = tx.value("status").toString();
    appendStatus(QString("status: %1").arg(status.isEmpty() ? "(unknown)" : status));

    if (isProven(status)) {
        m_pollTimer->stop();
        appendStatus("PROVEN — L2 state committed.");
        const QString address = currentAccountAddress();
        if (!address.isEmpty()) {
            applyBalance(m_engine->l2Balance(address));
        }
    } else if (isFailure(status)) {
        m_pollTimer->stop();
        const QString reason = tx.value("reason").toString();
        appendStatus(QString("Transaction %1%2")
                         .arg(status, reason.isEmpty() ? QString() : QString(": %1").arg(reason)));
    }
}
