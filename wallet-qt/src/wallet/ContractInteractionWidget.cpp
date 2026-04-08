#include "ContractInteractionWidget.h"

#include "WalletEngine.h"

#include <QtConcurrent/QtConcurrentRun>

#include <QCheckBox>
#include <QComboBox>
#include <QFormLayout>
#include <QGroupBox>
#include <QHBoxLayout>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLabel>
#include <QLineEdit>
#include <QMessageBox>
#include <QPlainTextEdit>
#include <QPushButton>
#include <QSettings>
#include <QVBoxLayout>

namespace {
constexpr const char* kRecentContractsKey = "WalletQt/recentContracts";
}

ContractInteractionWidget::ContractInteractionWidget(WalletEngine* engine, QWidget* parent)
    : QWidget(parent)
    , m_engine(engine)
    , m_readWatcher(new QFutureWatcher<QJsonObject>(this))
    , m_writeWatcher(new QFutureWatcher<QJsonObject>(this))
{
    auto* layout = new QVBoxLayout(this);

    auto* form = new QFormLayout();
    m_recentContractsCombo = new QComboBox(this);
    m_recentContractsCombo->addItem("Recent Contracts", "");
    const QStringList recent = QSettings().value(kRecentContractsKey).toStringList();
    for (const QString& item : recent) {
        m_recentContractsCombo->addItem(item, item);
    }
    form->addRow("Saved:", m_recentContractsCombo);

    m_contractAddressEdit = new QLineEdit(this);
    form->addRow("Contract Address:", m_contractAddressEdit);

    m_abiEdit = new QPlainTextEdit(this);
    m_abiEdit->setPlaceholderText("{\"abi\": [...]} or [...]");
    m_abiEdit->setFixedHeight(140);
    form->addRow("ABI / Schema:", m_abiEdit);

    m_methodCombo = new QComboBox(this);
    form->addRow("Method:", m_methodCombo);

    m_argsEdit = new QPlainTextEdit(this);
    m_argsEdit->setPlaceholderText("[] or {\"arg\": value}");
    m_argsEdit->setFixedHeight(90);
    form->addRow("Arguments:", m_argsEdit);

    m_rawModeCheck = new QCheckBox("Use raw payload mode", this);
    form->addRow("", m_rawModeCheck);

    m_rawPayloadEdit = new QLineEdit(this);
    m_rawPayloadEdit->setPlaceholderText("0x...");
    form->addRow("Raw Payload:", m_rawPayloadEdit);

    m_walletCombo = new QComboBox(this);
    form->addRow("Signing Wallet:", m_walletCombo);

    m_chainIdEdit = new QLineEdit(this);
    m_chainIdEdit->setText("1337");
    form->addRow("Chain ID:", m_chainIdEdit);

    m_maxFeeEdit = new QLineEdit(this);
    m_maxFeeEdit->setPlaceholderText("optional");
    form->addRow("Max Fee:", m_maxFeeEdit);
    layout->addLayout(form);

    auto* actions = new QHBoxLayout();
    m_readButton = new QPushButton("Read", this);
    m_writeButton = new QPushButton("Write", this);
    actions->addWidget(m_readButton);
    actions->addWidget(m_writeButton);
    actions->addStretch();
    layout->addLayout(actions);

    m_resultEdit = new QPlainTextEdit(this);
    m_resultEdit->setReadOnly(true);
    layout->addWidget(m_resultEdit);

    connect(m_recentContractsCombo, QOverload<int>::of(&QComboBox::currentIndexChanged), this, [this](int index) {
        const QString address = m_recentContractsCombo->itemData(index).toString();
        if (!address.isEmpty()) {
            m_contractAddressEdit->setText(address);
        }
    });
    connect(m_abiEdit, &QPlainTextEdit::textChanged, this, &ContractInteractionWidget::updateMethodList);
    connect(m_argsEdit, &QPlainTextEdit::textChanged, this, &ContractInteractionWidget::updatePreview);
    connect(m_methodCombo, &QComboBox::currentTextChanged, this, &ContractInteractionWidget::updatePreview);
    connect(m_rawPayloadEdit, &QLineEdit::textChanged, this, &ContractInteractionWidget::updatePreview);
    connect(m_rawModeCheck, &QCheckBox::toggled, this, &ContractInteractionWidget::updatePreview);
    connect(m_readButton, &QPushButton::clicked, this, &ContractInteractionWidget::onReadClicked);
    connect(m_writeButton, &QPushButton::clicked, this, &ContractInteractionWidget::onWriteClicked);
    connect(m_readWatcher, &QFutureWatcher<QJsonObject>::finished, this, &ContractInteractionWidget::handleReadFinished);
    connect(m_writeWatcher, &QFutureWatcher<QJsonObject>::finished, this, &ContractInteractionWidget::handleWriteFinished);
    connect(m_engine, &WalletEngine::accountAdded, this, [this](const WalletAccount&) { refreshWallets(); });
    connect(m_engine, &WalletEngine::accountRemoved, this, [this](const QString&) { refreshWallets(); });

    refreshWallets();
    updateMethodList();
}

void ContractInteractionWidget::refreshWallets()
{
    const QString current = m_walletCombo->currentData().toString();
    m_walletCombo->clear();
    for (const WalletAccount& account : m_engine->listAccounts()) {
        m_walletCombo->addItem(account.label, account.address);
    }
    const int idx = m_walletCombo->findData(current);
    if (idx >= 0) {
        m_walletCombo->setCurrentIndex(idx);
    }
}

void ContractInteractionWidget::updateMethodList()
{
    m_methodCombo->clear();
    const QByteArray abiBytes = m_abiEdit->toPlainText().toUtf8();
    if (abiBytes.trimmed().isEmpty()) {
        updatePreview();
        return;
    }
    QJsonParseError error;
    const QJsonDocument doc = QJsonDocument::fromJson(abiBytes, &error);
    if (error.error != QJsonParseError::NoError || (!doc.isArray() && !doc.isObject())) {
        updatePreview();
        return;
    }
    const QJsonArray abi = doc.isObject() ? doc.object().value("abi").toArray() : doc.array();
    for (const QJsonValue& value : abi) {
        const QJsonObject item = value.toObject();
        if (item.value("type").toString("function") == "function") {
            m_methodCombo->addItem(item.value("name").toString());
        }
    }
    updatePreview();
}

void ContractInteractionWidget::updatePreview()
{
    if (m_rawModeCheck->isChecked()) {
        m_resultEdit->setPlainText(QString("Raw payload preview:\n%1").arg(m_rawPayloadEdit->text().trimmed()));
        return;
    }
    QJsonParseError abiError;
    const QJsonDocument abiDoc = QJsonDocument::fromJson(m_abiEdit->toPlainText().toUtf8(), &abiError);
    QJsonParseError argsError;
    const QJsonDocument argsDoc = QJsonDocument::fromJson(m_argsEdit->toPlainText().isEmpty() ? "[]" : m_argsEdit->toPlainText().toUtf8(), &argsError);
    if (abiError.error != QJsonParseError::NoError || argsError.error != QJsonParseError::NoError || m_methodCombo->currentText().isEmpty()) {
        return;
    }
    QJsonObject request;
    request["contract_address"] = m_contractAddressEdit->text().trimmed();
    request["abi"] = abiDoc.isObject() ? abiDoc.object().value("abi").toArray() : abiDoc.array();
    request["method"] = m_methodCombo->currentText();
    request["args"] = argsDoc.isObject() ? QJsonValue(argsDoc.object()) : QJsonValue(argsDoc.array());
    const QJsonObject preview = m_engine->previewContractCall(request);
    if (!preview.isEmpty()) {
        m_resultEdit->setPlainText(QString("Calldata preview:\n%1").arg(preview.value("payload").toString()));
    }
}

void ContractInteractionWidget::onReadClicked()
{
    if (m_readWatcher->isRunning()) {
        return;
    }
    const QString address = m_contractAddressEdit->text().trimmed();
    if (address.isEmpty()) {
        QMessageBox::warning(this, "Missing Address", "Enter a contract address.");
        return;
    }
    QJsonObject request;
    request["contract_address"] = address;
    if (m_rawModeCheck->isChecked()) {
        request["data_hex"] = m_rawPayloadEdit->text().trimmed();
        request["sender"] = m_walletCombo->currentData().toString();
        m_readWatcher->setFuture(QtConcurrent::run([this, request]() {
            return m_engine->rawContractRead(request);
        }));
        return;
    }

    QJsonParseError abiError;
    const QJsonDocument abiDoc = QJsonDocument::fromJson(m_abiEdit->toPlainText().toUtf8(), &abiError);
    if (abiError.error != QJsonParseError::NoError || m_methodCombo->currentText().isEmpty()) {
        QMessageBox::warning(this, "Invalid ABI", "Provide a valid ABI and select a method.");
        return;
    }
    QJsonParseError argsError;
    const QJsonDocument argsDoc = QJsonDocument::fromJson(m_argsEdit->toPlainText().isEmpty() ? "[]" : m_argsEdit->toPlainText().toUtf8(), &argsError);
    if (argsError.error != QJsonParseError::NoError) {
        QMessageBox::warning(this, "Invalid Arguments", "Arguments must be valid JSON.");
        return;
    }
    request["chain_id"] = m_chainIdEdit->text().trimmed().toInt();
    request["contract_address"] = address;
    request["abi"] = abiDoc.isObject() ? abiDoc.object().value("abi").toArray() : abiDoc.array();
    request["method"] = m_methodCombo->currentText();
    request["args"] = argsDoc.isObject() ? QJsonValue(argsDoc.object()) : QJsonValue(argsDoc.array());
    request["sender"] = m_walletCombo->currentData().toString();
    m_readWatcher->setFuture(QtConcurrent::run([this, request]() {
        return m_engine->contractRead(request);
    }));
}

void ContractInteractionWidget::onWriteClicked()
{
    if (m_writeWatcher->isRunning()) {
        return;
    }
    const QString address = m_contractAddressEdit->text().trimmed();
    if (address.isEmpty()) {
        QMessageBox::warning(this, "Missing Address", "Enter a contract address.");
        return;
    }
    QJsonObject request;
    if (m_rawModeCheck->isChecked()) {
        request["from_address"] = m_walletCombo->currentData().toString();
        request["to_address"] = address;
        request["amount"] = "0.000000000";
        request["chain_id"] = m_chainIdEdit->text().trimmed().toInt();
        request["data_hex"] = m_rawPayloadEdit->text().trimmed();
        if (!m_maxFeeEdit->text().trimmed().isEmpty()) {
            request["max_fee"] = m_maxFeeEdit->text().trimmed().toLongLong();
        }
        m_writeWatcher->setFuture(QtConcurrent::run([this, request]() {
            return m_engine->submitTransaction(request);
        }));
        return;
    }
    QJsonParseError abiError;
    const QJsonDocument abiDoc = QJsonDocument::fromJson(m_abiEdit->toPlainText().toUtf8(), &abiError);
    if (abiError.error != QJsonParseError::NoError || m_methodCombo->currentText().isEmpty()) {
        QMessageBox::warning(this, "Invalid ABI", "Provide a valid ABI and select a method.");
        return;
    }
    QJsonParseError argsError;
    const QJsonDocument argsDoc = QJsonDocument::fromJson(m_argsEdit->toPlainText().isEmpty() ? "[]" : m_argsEdit->toPlainText().toUtf8(), &argsError);
    if (argsError.error != QJsonParseError::NoError) {
        QMessageBox::warning(this, "Invalid Arguments", "Arguments must be valid JSON.");
        return;
    }
    request["wallet_file"] = m_engine->walletFilePath();
    request["from_address"] = m_walletCombo->currentData().toString();
    request["contract_address"] = address;
    request["chain_id"] = m_chainIdEdit->text().trimmed().toInt();
    request["abi"] = abiDoc.isObject() ? abiDoc.object().value("abi").toArray() : abiDoc.array();
    request["method"] = m_methodCombo->currentText();
    request["args"] = argsDoc.isObject() ? QJsonValue(argsDoc.object()) : QJsonValue(argsDoc.array());
    if (!m_maxFeeEdit->text().trimmed().isEmpty()) {
        request["max_fee"] = m_maxFeeEdit->text().trimmed().toLongLong();
    }
    m_writeWatcher->setFuture(QtConcurrent::run([this, request]() {
        return m_engine->contractWrite(request);
    }));
}

void ContractInteractionWidget::handleReadFinished()
{
    const QJsonObject result = m_readWatcher->future().result();
    if (result.isEmpty()) {
        QMessageBox::warning(this, "Read Failed", "Contract read failed.");
        return;
    }
    rememberContract(m_contractAddressEdit->text().trimmed());
    m_resultEdit->setPlainText(QString::fromUtf8(QJsonDocument(result).toJson(QJsonDocument::Indented)));
}

void ContractInteractionWidget::handleWriteFinished()
{
    const QJsonObject result = m_writeWatcher->future().result();
    if (result.isEmpty()) {
        QMessageBox::warning(this, "Write Failed", "Contract write failed.");
        return;
    }
    rememberContract(m_contractAddressEdit->text().trimmed());
    m_resultEdit->setPlainText(QString::fromUtf8(QJsonDocument(result).toJson(QJsonDocument::Indented)));
    if (!result.value("tx_hash").toString().isEmpty()) {
        QMessageBox::information(this, "Contract Transaction Sent", result.value("tx_hash").toString());
    }
}

void ContractInteractionWidget::rememberContract(const QString& address)
{
    if (address.isEmpty()) {
        return;
    }
    QSettings settings;
    QStringList items = settings.value(kRecentContractsKey).toStringList();
    items.removeAll(address);
    items.prepend(address);
    while (items.size() > 10) {
        items.removeLast();
    }
    settings.setValue(kRecentContractsKey, items);
}
