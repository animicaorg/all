#include "SendWidget.h"
#include "WalletEngine.h"
#include "WalletDatabase.h"
#include "TransactionMonitor.h"
#include "../rpc/AnimicaRpcClient.h"
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QFormLayout>
#include <QGroupBox>
#include <QMessageBox>
#include "../rpc/RpcReply.h"
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>
#include <QEventLoop>
#include <QTimer>
#include <QDateTime>
#include <QDebug>

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
    , m_chainId(1337) // Default, will be updated
{
    setupUI();
    
    // Get chain ID
    RpcReply* reply = m_rpcClient->getChainId();
    connect(reply, &RpcReply::finished, [this, reply]() {
        if (reply->error() == QNetworkReply::NoError) {
            QJsonDocument doc = QJsonDocument::fromJson(reply->readAll());
            if (doc.isObject()) {
                QJsonObject obj = doc.object();
                if (obj.contains("result")) {
                    m_chainId = obj["result"].toInt(1337);
                }
            }
        }
        reply->deleteLater();
    });
    
    // Connect to balance updates
    connect(m_walletEngine, &WalletEngine::balanceUpdated,
            this, &SendWidget::onBalanceUpdated);
}

SendWidget::~SendWidget()
{
}

void SendWidget::setupUI()
{
    auto* mainLayout = new QVBoxLayout(this);
    
    // Title
    auto* titleLabel = new QLabel("Send Transaction", this);
    titleLabel->setStyleSheet("font-weight: bold; font-size: 16px;");
    mainLayout->addWidget(titleLabel);
    
    // Form group
    auto* formGroup = new QGroupBox("Transaction Details", this);
    auto* formLayout = new QFormLayout(formGroup);
    
    // From account
    m_fromAccountCombo = new QComboBox(this);
    m_fromAccountCombo->setMinimumWidth(300);
    formLayout->addRow("From Account:", m_fromAccountCombo);
    
    // Balance label (below from account)
    m_balanceLabel = new QLabel("Balance: 0.000000000 ANM", this);
    m_balanceLabel->setStyleSheet("color: #666; font-size: 12px;");
    formLayout->addRow("", m_balanceLabel);
    
    // To address
    auto* addressLayout = new QHBoxLayout();
    m_toAddressEdit = new QLineEdit(this);
    m_toAddressEdit->setPlaceholderText("anim1...");
    m_toAddressEdit->setMinimumWidth(400);
    addressLayout->addWidget(m_toAddressEdit);
    
    m_addressValidationLabel = new QLabel(this);
    addressLayout->addWidget(m_addressValidationLabel);
    addressLayout->addStretch();
    
    formLayout->addRow("To Address:", addressLayout);
    
    // Amount
    auto* amountLayout = new QHBoxLayout();
    m_amountSpinBox = new QDoubleSpinBox(this);
    m_amountSpinBox->setDecimals(9);
    m_amountSpinBox->setMinimum(0.000000001);
    m_amountSpinBox->setMaximum(1000000000.0);
    m_amountSpinBox->setSuffix(" ANM");
    m_amountSpinBox->setMinimumWidth(200);
    amountLayout->addWidget(m_amountSpinBox);
    
    m_maxButton = new QPushButton("Max", this);
    m_maxButton->setMaximumWidth(60);
    amountLayout->addWidget(m_maxButton);
    amountLayout->addStretch();
    
    formLayout->addRow("Amount:", amountLayout);
    
    // Amount validation label
    m_amountValidationLabel = new QLabel(this);
    m_amountValidationLabel->setStyleSheet("color: red; font-size: 11px;");
    formLayout->addRow("", m_amountValidationLabel);
    
    // Fee tier
    m_feeTierCombo = new QComboBox(this);
    m_feeTierCombo->addItem("Slow (Minimum Fee)", FeeEstimator::Slow);
    m_feeTierCombo->addItem("Normal (Recommended)", FeeEstimator::Normal);
    m_feeTierCombo->addItem("Fast (Priority)", FeeEstimator::Fast);
    m_feeTierCombo->setCurrentIndex(1); // Normal by default
    formLayout->addRow("Fee Tier:", m_feeTierCombo);
    
    // Fee display
    m_feeLabel = new QLabel("Est. Fee: --", this);
    m_feeLabel->setStyleSheet("color: #666;");
    formLayout->addRow("", m_feeLabel);
    
    // Fee warning
    m_feeWarningLabel = new QLabel(this);
    m_feeWarningLabel->setStyleSheet("color: orange; font-size: 11px;");
    formLayout->addRow("", m_feeWarningLabel);
    
    // Memo
    m_memoEdit = new QLineEdit(this);
    m_memoEdit->setPlaceholderText("Optional message");
    m_memoEdit->setMaxLength(256);
    formLayout->addRow("Memo:", m_memoEdit);
    
    mainLayout->addWidget(formGroup);
    
    // Send button
    auto* buttonLayout = new QHBoxLayout();
    buttonLayout->addStretch();
    m_sendButton = new QPushButton("Send Transaction", this);
    m_sendButton->setMinimumWidth(150);
    m_sendButton->setEnabled(false);
    buttonLayout->addWidget(m_sendButton);
    buttonLayout->addStretch();
    mainLayout->addLayout(buttonLayout);
    
    mainLayout->addStretch();
    
    // Connect signals
    connect(m_fromAccountCombo, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, &SendWidget::onAccountChanged);
    connect(m_toAddressEdit, &QLineEdit::textChanged,
            this, &SendWidget::onAddressChanged);
    connect(m_amountSpinBox, QOverload<double>::of(&QDoubleSpinBox::valueChanged),
            this, [this]() { onAmountChanged(); });
    connect(m_feeTierCombo, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, &SendWidget::onFeeTierChanged);
    connect(m_maxButton, &QPushButton::clicked,
            this, &SendWidget::onMaxClicked);
    connect(m_sendButton, &QPushButton::clicked,
            this, &SendWidget::onSendClicked);
    
    // Refresh accounts
    onAccountChanged(0);
}

void SendWidget::clearForm()
{
    m_toAddressEdit->clear();
    m_amountSpinBox->setValue(m_amountSpinBox->minimum());
    m_memoEdit->clear();
    m_feeTierCombo->setCurrentIndex(1); // Normal
    clearValidationErrors();
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
    if (!validateInputs()) {
        return;
    }
    
    // Get values
    QString accountId = getCurrentAccountId();
    QString toAddress = m_toAddressEdit->text().trimmed();
    qint64 amountWei = static_cast<qint64>(m_amountSpinBox->value() * 1e9);
    qint64 gasLimit = FeeEstimator::standardTransferGas();
    qint64 gasPrice = m_feeEstimator->getGasPrice(currentFeeTier());
    qint64 fee = gasPrice * gasLimit;
    qint64 total = amountWei + fee;
    
    // Confirmation dialog
    QString msg = QString(
        "Send %1 ANM to %2?\n\n"
        "Fee: %3 ANM\n"
        "Total: %4 ANM"
    ).arg(m_amountSpinBox->value(), 0, 'f', 9)
     .arg(toAddress)
     .arg(fee / 1e9, 0, 'f', 9)
     .arg(total / 1e9, 0, 'f', 9);
    
    int ret = QMessageBox::question(this, "Confirm Transaction", msg,
                                     QMessageBox::Yes | QMessageBox::No);
    if (ret != QMessageBox::Yes) {
        return;
    }
    
    // Get account
    WalletAccount account = m_walletEngine->getAccount(accountId);
    if (account.address.isEmpty()) {
        showError("Error", "Account not found");
        return;
    }
    
    // Get nonce
    RpcReply* nonceReply = m_rpcClient->getNonce(account.address, "pending");
    QEventLoop loop;
    QTimer timer;
    timer.setSingleShot(true);
    
    connect(nonceReply, &RpcReply::finished, &loop, &QEventLoop::quit);
    connect(&timer, &QTimer::timeout, &loop, &QEventLoop::quit);
    
    timer.start(10000);
    loop.exec();
    
    if (!timer.isActive()) {
        nonceReply->abort();
        nonceReply->deleteLater();
        showError("Error", "Timeout getting nonce");
        return;
    }
    timer.stop();
    
    if (nonceReply->error() != QNetworkReply::NoError) {
        QString error = nonceReply->errorString();
        nonceReply->deleteLater();
        showError("Error", "Failed to get nonce: " + error);
        return;
    }
    
    QByteArray nonceData = nonceReply->readAll();
    nonceReply->deleteLater();
    
    QJsonDocument nonceDoc = QJsonDocument::fromJson(nonceData);
    qint64 nonce = 0;
    if (nonceDoc.isObject()) {
        QJsonObject obj = nonceDoc.object();
        if (obj.contains("result")) {
            nonce = obj["result"].toVariant().toLongLong();
        }
    }
    
    // Build unsigned transaction
    QJsonObject unsignedTx;
    unsignedTx["version"] = 1;
    unsignedTx["chain_id"] = m_chainId;
    unsignedTx["sender"] = addressToHex(account.address);
    unsignedTx["nonce"] = QString::number(nonce);
    unsignedTx["gas_price"] = QString::number(gasPrice);
    unsignedTx["gas_limit"] = QString::number(gasLimit);
    unsignedTx["kind"] = 0; // TRANSFER
    
    QJsonObject payload;
    payload["to"] = addressToHex(toAddress);
    payload["amount"] = QString::number(amountWei);
    payload["data"] = QString::fromUtf8(m_memoEdit->text().toUtf8().toHex());
    unsignedTx["payload"] = payload;
    
    // Sign transaction
    QString signedHex = m_walletEngine->signTransaction(unsignedTx, accountId);
    if (signedHex.isEmpty()) {
        showError("Signing Failed", "Failed to sign transaction");
        return;
    }
    
    // Add to database as SIGNED (with temporary txid)
    QString tempTxid = "pending_" + QString::number(QDateTime::currentMSecsSinceEpoch());
    
    WalletTx dbTx;
    dbTx.txid = tempTxid;
    dbTx.direction = "out";
    dbTx.fromAccountId = accountId;
    dbTx.toAddress = toAddress;
    dbTx.amount = amountWei;
    dbTx.fee = fee;
    dbTx.state = "SIGNED";
    dbTx.firstSeenAt = QDateTime::currentMSecsSinceEpoch();
    dbTx.lastUpdateAt = dbTx.firstSeenAt;
    
    if (!m_database->addTransaction(dbTx)) {
        showError("Error", "Failed to save transaction");
        return;
    }
    
    // Reserve balance
    LedgerEntry pendingOut;
    pendingOut.txid = tempTxid;
    pendingOut.accountId = accountId;
    pendingOut.asset = "ANM";
    pendingOut.type = "PENDING_OUT";
    pendingOut.delta = -amountWei;
    pendingOut.stateVersion = m_database->nextStateVersion();
    pendingOut.createdAt = QDateTime::currentMSecsSinceEpoch();
    
    if (!m_database->addLedgerEntry(pendingOut)) {
        showError("Error", "Failed to reserve balance (insufficient funds)");
        m_database->deleteTransaction(tempTxid);
        return;
    }
    
    LedgerEntry feeReserved = pendingOut;
    feeReserved.type = "FEE_RESERVED";
    feeReserved.delta = -fee;
    feeReserved.stateVersion = m_database->nextStateVersion();
    
    if (!m_database->addLedgerEntry(feeReserved)) {
        showError("Error", "Failed to reserve fee");
        m_database->deleteTransaction(tempTxid);
        return;
    }
    
    // Broadcast transaction
    RpcReply* txReply = m_rpcClient->sendRawTransaction(signedHex);
    QEventLoop txLoop;
    QTimer txTimer;
    txTimer.setSingleShot(true);
    
    connect(txReply, &RpcReply::finished, &txLoop, &QEventLoop::quit);
    connect(&txTimer, &QTimer::timeout, &txLoop, &QEventLoop::quit);
    
    txTimer.start(10000);
    txLoop.exec();
    
    if (!txTimer.isActive()) {
        txReply->abort();
        txReply->deleteLater();
        showError("Error", "Timeout broadcasting transaction");
        // TODO: Revert ledger entries
        return;
    }
    txTimer.stop();
    
    if (txReply->error() != QNetworkReply::NoError) {
        QString error = txReply->errorString();
        txReply->deleteLater();
        showError("Broadcast Failed", "Failed to broadcast: " + error);
        // TODO: Revert ledger entries
        return;
    }
    
    QByteArray txData = txReply->readAll();
    txReply->deleteLater();
    
    QJsonDocument txDoc = QJsonDocument::fromJson(txData);
    QString txHash;
    
    if (txDoc.isObject()) {
        QJsonObject obj = txDoc.object();
        if (obj.contains("error")) {
            QJsonObject errorObj = obj["error"].toObject();
            QString errorMsg = errorObj["message"].toString();
            showError("Broadcast Failed", errorMsg);
            // TODO: Revert ledger entries
            return;
        }
        if (obj.contains("result")) {
            txHash = obj["result"].toString();
        }
    }
    
    if (txHash.isEmpty()) {
        showError("Error", "No transaction hash returned");
        // TODO: Revert ledger entries
        return;
    }
    
    // Update transaction with real txid
    dbTx.txid = txHash;
    dbTx.state = "BROADCAST";
    dbTx.lastUpdateAt = QDateTime::currentMSecsSinceEpoch();
    
    if (!m_database->updateTransaction(tempTxid, dbTx)) {
        qWarning() << "Failed to update transaction ID";
    }
    
    // TODO: Update ledger entries with real txid
    // This would require a method in WalletDatabase to update txid in ledger
    
    // Start monitoring
    if (m_monitor) {
        m_monitor->trackTransaction(txHash, "out");
    }
    
    // Show success
    showSuccess("Transaction Sent", "TX: " + txHash);
    emit transactionSent(txHash);
    
    // Clear form
    clearForm();
    updateBalanceLabel();
}

void SendWidget::onMaxClicked()
{
    qint64 available = getAvailableBalance();
    qint64 gasLimit = FeeEstimator::standardTransferGas();
    qint64 fee = m_feeEstimator->calculateFee(currentFeeTier(), gasLimit);
    qint64 maxAmount = available - fee;
    
    if (maxAmount > 0) {
        double maxAnm = maxAmount / 1e9;
        m_amountSpinBox->setValue(maxAnm);
    } else {
        m_amountSpinBox->setValue(0);
        showValidationError("amount", "Insufficient balance for fee");
    }
}

void SendWidget::onFeeTierChanged(int)
{
    updateFeeDisplay();
    validateInputs();
}

void SendWidget::onAddressChanged()
{
    QString address = m_toAddressEdit->text().trimmed();
    
    if (address.isEmpty()) {
        m_addressValidationLabel->clear();
    } else if (validateAddress(address)) {
        m_addressValidationLabel->setText("✓");
        m_addressValidationLabel->setStyleSheet("color: green; font-weight: bold;");
    } else {
        m_addressValidationLabel->setText("✗");
        m_addressValidationLabel->setStyleSheet("color: red; font-weight: bold;");
    }
    
    validateInputs();
}

void SendWidget::onAmountChanged()
{
    updateFeeDisplay();
    validateInputs();
}

void SendWidget::onAccountChanged(int)
{
    // Refresh account list
    m_fromAccountCombo->clear();
    
    if (!m_walletEngine || m_walletEngine->isLocked()) {
        m_balanceLabel->setText("Balance: (Locked)");
        m_sendButton->setEnabled(false);
        return;
    }
    
    auto accounts = m_walletEngine->listAccounts();
    for (const auto& account : accounts) {
        QString displayText = account.label + " (" + account.address + ")";
        m_fromAccountCombo->addItem(displayText, account.accountId);
    }

    // Ensure tracked balances are refreshed when account list changes
    // so placeholders don't persist in the wallet view.
    m_walletEngine->refreshBalances();

    updateBalanceLabel();
    updateFeeDisplay();
}

void SendWidget::onBalanceUpdated(const QString& address, const Balance&)
{
    QString currentAddress = getCurrentAccountAddress();
    if (currentAddress == address) {
        updateBalanceLabel();
    }
}

void SendWidget::updateFeeDisplay()
{
    qint64 gasLimit = FeeEstimator::standardTransferGas();
    qint64 fee = m_feeEstimator->calculateFee(currentFeeTier(), gasLimit);
    
    m_feeLabel->setText("Est. Fee: " + m_feeEstimator->formatFeeANM(fee));
    
    // Warn if fee > 1% of amount
    double amountAnm = m_amountSpinBox->value();
    double feeAnm = fee / 1e9;
    
    if (amountAnm > 0 && feeAnm > amountAnm * 0.01) {
        m_feeWarningLabel->setText("⚠ Fee is more than 1% of amount");
    } else {
        m_feeWarningLabel->clear();
    }
}

void SendWidget::updateBalanceLabel()
{
    QString accountId = getCurrentAccountId();
    if (accountId.isEmpty()) {
        m_balanceLabel->setText("Balance: 0.000000000 ANM");
        return;
    }
    
    qint64 available = getAvailableBalance();
    double availableAnm = available / 1e9;
    
    m_balanceLabel->setText(QString("Balance: %1 ANM").arg(availableAnm, 0, 'f', 9));
}

bool SendWidget::validateInputs()
{
    clearValidationErrors();
    
    if (!m_walletEngine || m_walletEngine->isLocked()) {
        m_sendButton->setEnabled(false);
        return false;
    }
    
    QString accountId = getCurrentAccountId();
    if (accountId.isEmpty()) {
        m_sendButton->setEnabled(false);
        return false;
    }
    
    // Validate address
    QString address = m_toAddressEdit->text().trimmed();
    if (address.isEmpty() || !validateAddress(address)) {
        m_sendButton->setEnabled(false);
        if (!address.isEmpty()) {
            showValidationError("address", "Invalid address format");
        }
        return false;
    }
    
    // Validate amount
    double amountAnm = m_amountSpinBox->value();
    if (amountAnm <= 0) {
        m_sendButton->setEnabled(false);
        return false;
    }
    
    qint64 amountWei = static_cast<qint64>(amountAnm * 1e9);
    qint64 fee = m_feeEstimator->calculateFee(currentFeeTier(), FeeEstimator::standardTransferGas());
    qint64 total = amountWei + fee;
    qint64 available = getAvailableBalance();
    
    if (total > available) {
        showValidationError("amount", "Insufficient balance (including fee)");
        m_sendButton->setEnabled(false);
        return false;
    }
    
    m_sendButton->setEnabled(true);
    return true;
}

bool SendWidget::validateAddress(const QString& address)
{
    // Check prefix
    if (!address.startsWith("anim1")) {
        return false;
    }
    
    // Check length (bech32m addresses should be at least 42 characters)
    if (address.length() < 42) {
        return false;
    }
    
    // TODO: Implement full bech32m checksum validation
    // For now, basic validation is sufficient
    
    return true;
}

void SendWidget::showValidationError(const QString& field, const QString& message)
{
    if (field == "address") {
        m_addressValidationLabel->setText("✗ " + message);
        m_addressValidationLabel->setStyleSheet("color: red; font-size: 11px;");
    } else if (field == "amount") {
        m_amountValidationLabel->setText("✗ " + message);
        m_amountValidationLabel->setStyleSheet("color: red; font-size: 11px;");
    }
}

void SendWidget::clearValidationErrors()
{
    m_amountValidationLabel->clear();
    m_feeWarningLabel->clear();
    onAddressChanged(); // Revalidate address
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

QString SendWidget::addressToHex(const QString& bech32mAddress)
{
    // Convert bech32m address to hex
    // This is a placeholder implementation
    // In production, this should properly decode bech32m
    
    if (bech32mAddress.startsWith("anim1")) {
        // Remove prefix and convert to hex
        QString data = bech32mAddress.mid(5);
        // For now, just return as-is with 0x prefix
        // TODO: Implement proper bech32m decoding
        return "0x" + data;
    }
    
    return bech32mAddress;
}

QString SendWidget::getCurrentAccountId() const
{
    if (m_fromAccountCombo->currentIndex() < 0) {
        return QString();
    }
    return m_fromAccountCombo->currentData().toString();
}

QString SendWidget::getCurrentAccountAddress() const
{
    QString accountId = getCurrentAccountId();
    if (accountId.isEmpty()) {
        return QString();
    }
    
    WalletAccount account = m_walletEngine->getAccount(accountId);
    return account.address;
}

qint64 SendWidget::getAvailableBalance() const
{
    QString accountId = getCurrentAccountId();
    if (accountId.isEmpty()) {
        return 0;
    }
    
    if (!m_database) {
        return 0;
    }
    
    return m_database->getBalance(accountId, "ANM");
}

FeeEstimator::FeeTier SendWidget::currentFeeTier() const
{
    int index = m_feeTierCombo->currentIndex();
    if (index < 0) {
        return FeeEstimator::Normal;
    }
    return static_cast<FeeEstimator::FeeTier>(m_feeTierCombo->itemData(index).toInt());
}
