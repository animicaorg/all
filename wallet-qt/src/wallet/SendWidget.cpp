#include "SendWidget.h"

#include "TransactionMonitor.h"
#include "WalletDatabase.h"
#include "WalletEngine.h"
#include "../rpc/AnimicaRpcClient.h"
#include "../rpc/RpcReply.h"
#include "../rpc/RpcSettings.h"

#include <QtConcurrent/QtConcurrentRun>

#include <QDateTime>
#include <QCompleter>
#include <QFormLayout>
#include <QGroupBox>
#include <QJsonDocument>
#include <QHBoxLayout>
#include <QJsonObject>
#include <QMessageBox>
#include <QRegularExpression>
#include <QSignalBlocker>
#include <QSettings>
#include <QStringListModel>
#include <QVBoxLayout>

SendWidget::SendWidget(
    WalletEngine* walletEngine,
    AnimicaRpcClient* rpcClient,
    WalletDatabase* database,
    TransactionMonitor* monitor,
    QWidget* parent
)
    : QWidget(parent)
    , m_walletEngine(walletEngine)
    , m_rpcClient(rpcClient)
    , m_database(database)
    , m_monitor(monitor)
    , m_feeEstimator(new FeeEstimator(rpcClient, this))
    , m_sendWatcher(new QFutureWatcher<QJsonObject>(this))
    , m_chainId(RpcSettings::canonicalChainId())
{
    setupUI();

    connect(m_sendWatcher, &QFutureWatcher<QJsonObject>::finished, this, &SendWidget::handleSendFinished);
    connect(m_walletEngine, &WalletEngine::balanceUpdated, this, &SendWidget::onBalanceUpdated);
    connect(m_walletEngine, &WalletEngine::accountAdded, this, [this](const WalletAccount&) { refreshAccounts(); });
    connect(m_walletEngine, &WalletEngine::accountRemoved, this, [this](const QString&) { refreshAccounts(); });
    connect(m_walletEngine, &WalletEngine::accountUpdated, this, [this](const WalletAccount&) { refreshAccounts(); });
    connect(m_walletEngine, &WalletEngine::contactAdded, this, [this](const Contact&) { updateRecipientCompleter(); });
    connect(m_walletEngine, &WalletEngine::contactUpdated, this, [this](const Contact&) { updateRecipientCompleter(); });
    connect(m_walletEngine, &WalletEngine::contactRemoved, this, [this](const QString&) { updateRecipientCompleter(); });

    refreshAccounts();
}

SendWidget::~SendWidget() = default;

void SendWidget::setupUI()
{
    auto* mainLayout = new QVBoxLayout(this);

    auto* titleLabel = new QLabel("Send Transaction", this);
    titleLabel->setStyleSheet("font-weight: bold; font-size: 16px;");
    mainLayout->addWidget(titleLabel);

    auto* formGroup = new QGroupBox("Transaction Details", this);
    auto* formLayout = new QFormLayout(formGroup);

    m_fromAccountCombo = new QComboBox(this);
    m_fromAccountCombo->setMinimumWidth(320);
    formLayout->addRow("From Wallet:", m_fromAccountCombo);

    m_balanceLabel = new QLabel("Balance: 0.000000000 ANM", this);
    m_balanceLabel->setStyleSheet("color: #666; font-size: 12px;");
    formLayout->addRow("", m_balanceLabel);

    auto* addressLayout = new QHBoxLayout();
    m_toAddressEdit = new QLineEdit(this);
    m_toAddressEdit->setPlaceholderText("anim1...");
    m_toAddressEdit->setMinimumWidth(420);
    addressLayout->addWidget(m_toAddressEdit);
    m_addressValidationLabel = new QLabel(this);
    addressLayout->addWidget(m_addressValidationLabel);
    addressLayout->addStretch();
    formLayout->addRow("Recipient:", addressLayout);

    auto* amountLayout = new QHBoxLayout();
    m_amountSpinBox = new QDoubleSpinBox(this);
    m_amountSpinBox->setDecimals(9);
    m_amountSpinBox->setMinimum(0.000000001);
    m_amountSpinBox->setMaximum(1000000000.0);
    m_amountSpinBox->setSuffix(" ANM");
    m_amountSpinBox->setMinimumWidth(200);
    amountLayout->addWidget(m_amountSpinBox);
    m_maxButton = new QPushButton("Max", this);
    amountLayout->addWidget(m_maxButton);
    amountLayout->addStretch();
    formLayout->addRow("Amount:", amountLayout);

    m_amountValidationLabel = new QLabel(this);
    m_amountValidationLabel->setStyleSheet("color: #b91c1c; font-size: 11px;");
    formLayout->addRow("", m_amountValidationLabel);

    m_feeTierCombo = new QComboBox(this);
    m_feeTierCombo->addItem("Slow", FeeEstimator::Slow);
    m_feeTierCombo->addItem("Normal", FeeEstimator::Normal);
    m_feeTierCombo->addItem("Fast", FeeEstimator::Fast);
    m_feeTierCombo->setCurrentIndex(1);
    formLayout->addRow("Fee Tier:", m_feeTierCombo);

    m_feeLabel = new QLabel("Max Fee: --", this);
    m_feeLabel->setStyleSheet("color: #666;");
    formLayout->addRow("", m_feeLabel);

    m_feeWarningLabel = new QLabel(this);
    m_feeWarningLabel->setStyleSheet("color: #b45309; font-size: 11px;");
    formLayout->addRow("", m_feeWarningLabel);

    auto* advancedGroup = new QGroupBox("Advanced", this);
    auto* advancedLayout = new QFormLayout(advancedGroup);

    m_nonceEdit = new QLineEdit(this);
    m_nonceEdit->setPlaceholderText("auto");
    advancedLayout->addRow("Nonce Override:", m_nonceEdit);

    m_validAfterEdit = new QLineEdit(this);
    m_validAfterEdit->setPlaceholderText("head height");
    advancedLayout->addRow("Valid After:", m_validAfterEdit);

    m_validUntilEdit = new QLineEdit(this);
    m_validUntilEdit->setPlaceholderText("head + ttl");
    advancedLayout->addRow("Valid Until:", m_validUntilEdit);

    m_dataPayloadEdit = new QLineEdit(this);
    m_dataPayloadEdit->setPlaceholderText("0x... raw call data / payload");
    advancedLayout->addRow("Raw Payload:", m_dataPayloadEdit);

    m_memoEdit = new QLineEdit(this);
    m_memoEdit->setPlaceholderText("Local note only");
    advancedLayout->addRow("Local Note:", m_memoEdit);

    formLayout->addRow(advancedGroup);
    mainLayout->addWidget(formGroup);

    m_statusLabel = new QLabel(this);
    m_statusLabel->setStyleSheet("color: #666;");
    mainLayout->addWidget(m_statusLabel);

    auto* buttonLayout = new QHBoxLayout();
    buttonLayout->addStretch();
    m_sendButton = new QPushButton("Send Transaction", this);
    buttonLayout->addWidget(m_sendButton);
    mainLayout->addLayout(buttonLayout);
    mainLayout->addStretch();

    connect(m_fromAccountCombo, QOverload<int>::of(&QComboBox::currentIndexChanged), this, &SendWidget::onAccountChanged);
    connect(m_toAddressEdit, &QLineEdit::textChanged, this, &SendWidget::onAddressChanged);
    connect(m_amountSpinBox, QOverload<double>::of(&QDoubleSpinBox::valueChanged), this, [this]() { onAmountChanged(); });
    connect(m_feeTierCombo, QOverload<int>::of(&QComboBox::currentIndexChanged), this, &SendWidget::onFeeTierChanged);
    connect(m_maxButton, &QPushButton::clicked, this, &SendWidget::onMaxClicked);
    connect(m_sendButton, &QPushButton::clicked, this, &SendWidget::onSendClicked);
    connect(m_nonceEdit, &QLineEdit::textChanged, this, [this]() { validateInputs(); });
    connect(m_validAfterEdit, &QLineEdit::textChanged, this, [this]() { validateInputs(); });
    connect(m_validUntilEdit, &QLineEdit::textChanged, this, [this]() { validateInputs(); });
    connect(m_dataPayloadEdit, &QLineEdit::textChanged, this, [this]() { validateInputs(); });

    updateRecipientCompleter();
}

void SendWidget::clearForm()
{
    m_toAddressEdit->clear();
    m_amountSpinBox->setValue(m_amountSpinBox->minimum());
    m_memoEdit->clear();
    m_nonceEdit->clear();
    m_validAfterEdit->clear();
    m_validUntilEdit->clear();
    m_dataPayloadEdit->clear();
    m_feeTierCombo->setCurrentIndex(1);
    clearValidationErrors();
    m_statusLabel->clear();
}

void SendWidget::setRecipientAddress(const QString& address)
{
    m_toAddressEdit->setText(address);
}

void SendWidget::setAmount(double amount)
{
    m_amountSpinBox->setValue(amount);
}

void SendWidget::onSendClicked()
{
    if (!validateInputs() || m_sendWatcher->isRunning()) {
        return;
    }

    const QString accountId = getCurrentAccountId();
    const QString fromAddress = getCurrentAccountAddress();
    const QString toAddress = normalizedRecipientAddress();
    const QString amountText = QString::number(m_amountSpinBox->value(), 'f', 9);
    const qint64 gasLimit = FeeEstimator::standardTransferGas();
    const qint64 maxFee = m_feeEstimator->getGasPrice(currentFeeTier());
    const qint64 totalBase = static_cast<qint64>(m_amountSpinBox->value() * 1e9) + maxFee;

    const QString confirmation = QString(
        "Send %1 ANM from\n%2\n\nto\n%3\n\nMax fee: %4 ANM\nTotal reserved: %5 ANM"
    )
        .arg(amountText)
        .arg(fromAddress)
        .arg(toAddress)
        .arg(maxFee / 1e9, 0, 'f', 9)
        .arg(totalBase / 1e9, 0, 'f', 9);
    if (QMessageBox::question(this, "Confirm Transaction", confirmation, QMessageBox::Yes | QMessageBox::No) != QMessageBox::Yes) {
        return;
    }

    QJsonObject request;
    request["from_address"] = fromAddress;
    request["to_address"] = toAddress;
    request["amount"] = amountText;
    request["gas_limit"] = static_cast<qint64>(gasLimit);
    request["max_fee"] = maxFee;
    request["chain_id"] = m_chainId;
    if (!m_nonceEdit->text().trimmed().isEmpty()) {
        request["nonce"] = m_nonceEdit->text().trimmed().toLongLong();
    }
    if (!m_validAfterEdit->text().trimmed().isEmpty()) {
        request["valid_after"] = m_validAfterEdit->text().trimmed().toLongLong();
    }
    if (!m_validUntilEdit->text().trimmed().isEmpty()) {
        request["valid_until"] = m_validUntilEdit->text().trimmed().toLongLong();
    }
    if (!m_dataPayloadEdit->text().trimmed().isEmpty()) {
        request["data_hex"] = m_dataPayloadEdit->text().trimmed();
    }

    m_sendWatcher->setProperty("accountId", accountId);
    m_sendWatcher->setProperty("toAddress", toAddress);
    m_sendWatcher->setProperty("amountBase", static_cast<qlonglong>(m_amountSpinBox->value() * 1e9));
    m_sendWatcher->setProperty("maxFee", static_cast<qlonglong>(maxFee));

    m_statusLabel->setText("Submitting transaction...");
    m_sendButton->setEnabled(false);

    WalletEngine* engine = m_walletEngine;
    m_sendWatcher->setFuture(QtConcurrent::run([engine, request]() {
        return engine->submitTransaction(request);
    }));
}

void SendWidget::handleSendFinished()
{
    m_sendButton->setEnabled(true);

    const QJsonObject result = m_sendWatcher->future().result();
    if (result.isEmpty()) {
        showError("Send Failed", "The transaction was not admitted by the node.");
        m_statusLabel->setText("Transaction failed.");
        return;
    }

    const QString txHash = result.value("tx_hash").toString();
    if (txHash.isEmpty()) {
        showError("Send Failed", "The node did not return a transaction hash.");
        m_statusLabel->setText("Transaction failed.");
        return;
    }

    const QString accountId = m_sendWatcher->property("accountId").toString();
    const QString toAddress = m_sendWatcher->property("toAddress").toString();
    const qint64 amountBase = m_sendWatcher->property("amountBase").toLongLong();
    const qint64 maxFee = m_sendWatcher->property("maxFee").toLongLong();

    if (m_database) {
        WalletTx tx;
        tx.txid = txHash;
        tx.direction = "out";
        tx.fromAccountId = accountId;
        tx.toAddress = toAddress;
        tx.amount = amountBase;
        tx.fee = maxFee;
        tx.state = result.value("mempool_admitted").toBool() ? "MEMPOOL" : "BROADCAST";
        tx.firstSeenAt = QDateTime::currentMSecsSinceEpoch();
        tx.lastUpdateAt = tx.firstSeenAt;
        const QString rawTx = result.value("raw_transaction").toString();
        tx.rawTx = rawTx.startsWith("0x") ? QByteArray::fromHex(rawTx.mid(2).toLatin1()) : QByteArray::fromHex(rawTx.toLatin1());
        m_database->addTransaction(tx);

        LedgerEntry pendingOut;
        pendingOut.txid = txHash;
        pendingOut.accountId = accountId;
        pendingOut.asset = "ANM";
        pendingOut.type = "PENDING_OUT";
        pendingOut.delta = -amountBase;
        pendingOut.stateVersion = m_database->nextStateVersion();
        pendingOut.createdAt = tx.firstSeenAt;
        m_database->addLedgerEntry(pendingOut);

        LedgerEntry feeReserved = pendingOut;
        feeReserved.type = "FEE_RESERVED";
        feeReserved.delta = -maxFee;
        feeReserved.stateVersion = m_database->nextStateVersion();
        m_database->addLedgerEntry(feeReserved);
    }

    if (m_monitor) {
        m_monitor->trackTransaction(txHash, "out");
    }

    QSettings settings;
    QStringList recent = settings.value("WalletQt/recentRecipients").toStringList();
    recent.removeAll(toAddress);
    recent.prepend(toAddress);
    while (recent.size() > 10) {
        recent.removeLast();
    }
    settings.setValue("WalletQt/recentRecipients", recent);
    updateRecipientCompleter();

    m_statusLabel->setText(QString("Submitted: %1").arg(txHash));
    showSuccess("Transaction Sent", txHash);
    emit transactionSent(txHash);
    clearForm();
    updateBalanceLabel();
}

void SendWidget::onMaxClicked()
{
    const qint64 available = getAvailableBalance();
    const qint64 fee = m_feeEstimator->calculateFee(currentFeeTier(), FeeEstimator::standardTransferGas());
    const qint64 maxAmount = qMax<qint64>(0, available - fee);
    m_amountSpinBox->setValue(maxAmount / 1e9);
}

void SendWidget::onFeeTierChanged(int)
{
    updateFeeDisplay();
    validateInputs();
}

void SendWidget::onAddressChanged()
{
    const QString address = m_toAddressEdit->text().trimmed();
    if (address.isEmpty()) {
        m_addressValidationLabel->clear();
    } else if (validateAddress(address)) {
        m_addressValidationLabel->setText("Valid");
        m_addressValidationLabel->setStyleSheet("color: #15803d; font-weight: bold;");
    } else {
        m_addressValidationLabel->setText("Invalid");
        m_addressValidationLabel->setStyleSheet("color: #b91c1c; font-weight: bold;");
    }
    validateInputs();
}

void SendWidget::onAmountChanged()
{
    updateFeeDisplay();
    validateInputs();
}

void SendWidget::refreshAccounts()
{
    const QString previousAccountId = getCurrentAccountId();
    QSignalBlocker blocker(m_fromAccountCombo);
    m_fromAccountCombo->clear();

    if (!m_walletEngine || m_walletEngine->isLocked()) {
        blocker.unblock();
        m_balanceLabel->setText("Balance: unavailable");
        m_sendButton->setEnabled(false);
        return;
    }

    const auto accounts = m_walletEngine->listAccounts();
    int selectedIndex = -1;
    int defaultIndex = -1;
    for (const WalletAccount& account : accounts) {
        const QString label = account.isDefault
            ? QString("%1 (Default)").arg(account.label)
            : account.label;
        m_fromAccountCombo->addItem(QString("%1 | %2").arg(label, account.address), account.accountId);
        const int row = m_fromAccountCombo->count() - 1;
        if (!previousAccountId.isEmpty() && account.accountId == previousAccountId) {
            selectedIndex = row;
        }
        if (account.isDefault) {
            defaultIndex = row;
        }
    }

    if (selectedIndex < 0) {
        selectedIndex = defaultIndex >= 0 ? defaultIndex : (m_fromAccountCombo->count() > 0 ? 0 : -1);
    }
    if (selectedIndex >= 0) {
        m_fromAccountCombo->setCurrentIndex(selectedIndex);
    }

    blocker.unblock();
    onAccountChanged(m_fromAccountCombo->currentIndex());
}

void SendWidget::onAccountChanged(int)
{
    if (!m_walletEngine || m_walletEngine->isLocked() || getCurrentAccountId().isEmpty()) {
        m_balanceLabel->setText("Balance: unavailable");
        m_sendButton->setEnabled(false);
        return;
    }

    m_walletEngine->refreshBalances();
    updateBalanceLabel();
    updateFeeDisplay();
    validateInputs();
}

void SendWidget::onBalanceUpdated(const QString& address, const Balance&)
{
    if (address == getCurrentAccountAddress()) {
        updateBalanceLabel();
        validateInputs();
    }
}

void SendWidget::updateFeeDisplay()
{
    const qint64 fee = m_feeEstimator->calculateFee(currentFeeTier(), FeeEstimator::standardTransferGas());
    m_feeLabel->setText("Max Fee: " + m_feeEstimator->formatFeeANM(fee));
    if (m_amountSpinBox->value() > 0 && (fee / 1e9) > (m_amountSpinBox->value() * 0.01)) {
        m_feeWarningLabel->setText("Fee reserve is more than 1% of the transfer amount.");
    } else {
        m_feeWarningLabel->clear();
    }
}

void SendWidget::updateBalanceLabel()
{
    const qint64 available = getAvailableBalance();
    const QString address = getCurrentAccountAddress();
    const Balance balance = m_walletEngine->getBalance(address);
    m_balanceLabel->setText(
        QString("Confirmed: %1 ANM | Available: %2 ANM")
            .arg(balance.confirmed / 1e9, 0, 'f', 9)
            .arg(available / 1e9, 0, 'f', 9)
    );
}

void SendWidget::updateRecipientCompleter()
{
    QStringList candidates;
    for (const Contact& contact : m_walletEngine->listContacts()) {
        if (!contact.label.isEmpty()) {
            candidates << QString("%1 <%2>").arg(contact.label, contact.address);
        }
        candidates << contact.address;
    }
    const QStringList recent = QSettings().value("WalletQt/recentRecipients").toStringList();
    for (const QString& item : recent) {
        if (!candidates.contains(item)) {
            candidates << item;
        }
    }
    auto* model = new QStringListModel(candidates, m_toAddressEdit);
    auto* completer = new QCompleter(model, m_toAddressEdit);
    completer->setCaseSensitivity(Qt::CaseInsensitive);
    completer->setFilterMode(Qt::MatchContains);
    m_toAddressEdit->setCompleter(completer);
}

bool SendWidget::validateInputs()
{
    clearValidationErrors();
    if (!m_walletEngine || m_walletEngine->isLocked() || m_sendWatcher->isRunning()) {
        m_sendButton->setEnabled(false);
        return false;
    }

    const QString accountId = getCurrentAccountId();
    if (accountId.isEmpty()) {
        m_sendButton->setEnabled(false);
        return false;
    }

    const QString address = normalizedRecipientAddress();
    if (address.isEmpty() || !validateAddress(address)) {
        if (!address.isEmpty()) {
            showValidationError("address", "Recipient address is invalid.");
        }
        m_sendButton->setEnabled(false);
        return false;
    }

    const double amountAnm = m_amountSpinBox->value();
    if (amountAnm <= 0) {
        showValidationError("amount", "Amount must be greater than zero.");
        m_sendButton->setEnabled(false);
        return false;
    }

    bool ok = true;
    const qint64 amountBase = static_cast<qint64>(amountAnm * 1e9);
    const qint64 fee = m_feeEstimator->calculateFee(currentFeeTier(), FeeEstimator::standardTransferGas());
    const qint64 available = getAvailableBalance();
    if (available < amountBase + fee) {
        ok = false;
        showValidationError("amount", "Insufficient available balance for amount plus fee.");
    }

    auto parseOptionalInt = [&ok, this](QLineEdit* edit, const QString& label) -> qint64 {
        const QString text = edit->text().trimmed();
        if (text.isEmpty()) {
            return -1;
        }
        bool localOk = false;
        const qint64 value = text.toLongLong(&localOk);
        if (!localOk || value < 0) {
            ok = false;
            showValidationError("amount", QString("%1 must be a non-negative integer.").arg(label));
        }
        return value;
    };
    const qint64 validAfter = parseOptionalInt(m_validAfterEdit, "Valid After");
    const qint64 validUntil = parseOptionalInt(m_validUntilEdit, "Valid Until");
    if (validAfter >= 0 && validUntil >= 0 && validUntil <= validAfter) {
        ok = false;
        showValidationError("amount", "Valid Until must be greater than Valid After.");
    }

    QString payload = m_dataPayloadEdit->text().trimmed();
    if (payload.startsWith("0x")) {
        payload = payload.mid(2);
    }
    if (!payload.isEmpty()) {
        const QRegularExpression hexPattern("^[0-9a-fA-F]+$");
        if (!hexPattern.match(payload).hasMatch() || payload.size() % 2 != 0) {
            ok = false;
            showValidationError("amount", "Raw payload must be even-length hexadecimal.");
        }
    }

    m_sendButton->setEnabled(ok);
    return ok;
}

bool SendWidget::validateAddress(const QString& address)
{
    return m_walletEngine && m_walletEngine->validateAddress(address);
}

void SendWidget::showValidationError(const QString& field, const QString& message)
{
    if (field == "address") {
        m_addressValidationLabel->setText(message);
        m_addressValidationLabel->setStyleSheet("color: #b91c1c; font-size: 11px;");
        return;
    }
    m_amountValidationLabel->setText(message);
}

void SendWidget::clearValidationErrors()
{
    m_amountValidationLabel->clear();
}

void SendWidget::showError(const QString& title, const QString& message)
{
    QMessageBox::critical(this, title, message);
    emit error(message);
}

void SendWidget::showSuccess(const QString& title, const QString& message)
{
    QMessageBox::information(this, title, message);
}

QString SendWidget::normalizedRecipientAddress() const
{
    QString address = m_toAddressEdit->text().trimmed();
    const int left = address.indexOf('<');
    const int right = address.indexOf('>');
    if (left >= 0 && right > left) {
        address = address.mid(left + 1, right - left - 1).trimmed();
    }
    return address;
}

QString SendWidget::getCurrentAccountId() const
{
    return m_fromAccountCombo->currentData().toString();
}

QString SendWidget::getCurrentAccountAddress() const
{
    const WalletAccount account = m_walletEngine->getAccount(getCurrentAccountId());
    return account.address;
}

qint64 SendWidget::getAvailableBalance() const
{
    const QString accountId = getCurrentAccountId();
    const QString address = getCurrentAccountAddress();
    qint64 confirmed = static_cast<qint64>(m_walletEngine->getBalance(address).confirmed);
    qint64 reserved = 0;
    if (m_database && !accountId.isEmpty()) {
        const QList<LedgerEntry> entries = m_database->listLedgerEntries();
        for (const LedgerEntry& entry : entries) {
            if (entry.accountId != accountId) {
                continue;
            }
            if ((entry.type == "PENDING_OUT" || entry.type == "FEE_RESERVED") && entry.delta < 0) {
                reserved += -entry.delta;
            }
        }
    }
    return qMax<qint64>(0, confirmed - reserved);
}

FeeEstimator::FeeTier SendWidget::currentFeeTier() const
{
    return static_cast<FeeEstimator::FeeTier>(m_feeTierCombo->currentData().toInt());
}
