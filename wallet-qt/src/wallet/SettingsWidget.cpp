#include "SettingsWidget.h"

#include <QComboBox>
#include <QDir>
#include <QFile>
#include <QFileDialog>
#include <QFormLayout>
#include <QGroupBox>
#include <QHBoxLayout>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLabel>
#include <QLineEdit>
#include <QMessageBox>
#include <QPlainTextEdit>
#include <QPushButton>
#include <QSettings>
#include <QSpinBox>
#include <QUrl>
#include <QVBoxLayout>

namespace {
constexpr const char* kGroup = "WalletQt";
}

SettingsWidget::SettingsWidget(const QString& walletFilePath, const QString& dataDir, QWidget* parent)
    : QWidget(parent)
    , m_walletFilePath(walletFilePath)
    , m_dataDir(dataDir)
{
    auto* layout = new QVBoxLayout(this);

    auto* networkGroup = new QGroupBox("Network", this);
    auto* networkLayout = new QFormLayout(networkGroup);

    m_networkCombo = new QComboBox(this);
    m_networkCombo->addItem("Mainnet", "mainnet");
    m_networkCombo->addItem("Testnet", "testnet");
    m_networkCombo->addItem("Devnet", "devnet");
    m_networkCombo->addItem("Custom", "custom");
    networkLayout->addRow("Network:", m_networkCombo);

    m_rpcUrlEdit = new QLineEdit(this);
    networkLayout->addRow("RPC URL:", m_rpcUrlEdit);

    m_chainIdSpin = new QSpinBox(this);
    m_chainIdSpin->setRange(1, 1000000);
    networkLayout->addRow("Chain ID:", m_chainIdSpin);

    m_explorerUrlEdit = new QLineEdit(this);
    networkLayout->addRow("Explorer URL:", m_explorerUrlEdit);

    m_fallbackRpcEdit = new QPlainTextEdit(this);
    m_fallbackRpcEdit->setPlaceholderText("One fallback RPC URL per line");
    m_fallbackRpcEdit->setMaximumBlockCount(32);
    m_fallbackRpcEdit->setFixedHeight(70);
    networkLayout->addRow("Fallback RPCs:", m_fallbackRpcEdit);
    layout->addWidget(networkGroup);

    auto* runtimeGroup = new QGroupBox("Runtime", this);
    auto* runtimeLayout = new QFormLayout(runtimeGroup);

    m_pollIntervalSpin = new QSpinBox(this);
    m_pollIntervalSpin->setRange(1000, 600000);
    m_pollIntervalSpin->setSingleStep(1000);
    runtimeLayout->addRow("Polling Interval (ms):", m_pollIntervalSpin);

    m_timeoutSpin = new QSpinBox(this);
    m_timeoutSpin->setRange(1000, 120000);
    m_timeoutSpin->setSingleStep(1000);
    runtimeLayout->addRow("RPC Timeout (ms):", m_timeoutSpin);

    m_logLevelCombo = new QComboBox(this);
    m_logLevelCombo->addItems({"DEBUG", "INFO", "WARNING", "ERROR"});
    runtimeLayout->addRow("Log Level:", m_logLevelCombo);

    m_walletFileLabel = new QLabel(this);
    m_walletFileLabel->setTextInteractionFlags(Qt::TextSelectableByMouse);
    runtimeLayout->addRow("Wallet File:", m_walletFileLabel);

    m_dataDirLabel = new QLabel(this);
    m_dataDirLabel->setTextInteractionFlags(Qt::TextSelectableByMouse);
    runtimeLayout->addRow("Data Directory:", m_dataDirLabel);
    layout->addWidget(runtimeGroup);

    auto* effectiveGroup = new QGroupBox("Effective Config", this);
    auto* effectiveLayout = new QVBoxLayout(effectiveGroup);
    m_effectiveConfigEdit = new QPlainTextEdit(this);
    m_effectiveConfigEdit->setReadOnly(true);
    effectiveLayout->addWidget(m_effectiveConfigEdit);
    layout->addWidget(effectiveGroup);

    auto* buttons = new QHBoxLayout();
    m_importButton = new QPushButton("Import Settings", this);
    m_exportButton = new QPushButton("Export Settings", this);
    m_defaultsButton = new QPushButton("Restore Defaults", this);
    m_saveButton = new QPushButton("Save", this);
    buttons->addWidget(m_importButton);
    buttons->addWidget(m_exportButton);
    buttons->addStretch();
    buttons->addWidget(m_defaultsButton);
    buttons->addWidget(m_saveButton);
    layout->addLayout(buttons);

    connect(m_saveButton, &QPushButton::clicked, this, &SettingsWidget::onSaveClicked);
    connect(m_defaultsButton, &QPushButton::clicked, this, &SettingsWidget::onDefaultsClicked);
    connect(m_exportButton, &QPushButton::clicked, this, &SettingsWidget::onExportClicked);
    connect(m_importButton, &QPushButton::clicked, this, &SettingsWidget::onImportClicked);
    connect(m_networkCombo, &QComboBox::currentTextChanged, this, &SettingsWidget::updateEffectiveConfig);
    connect(m_rpcUrlEdit, &QLineEdit::textChanged, this, &SettingsWidget::updateEffectiveConfig);
    connect(m_explorerUrlEdit, &QLineEdit::textChanged, this, &SettingsWidget::updateEffectiveConfig);
    connect(m_fallbackRpcEdit, &QPlainTextEdit::textChanged, this, &SettingsWidget::updateEffectiveConfig);
    connect(m_chainIdSpin, QOverload<int>::of(&QSpinBox::valueChanged), this, &SettingsWidget::updateEffectiveConfig);
    connect(m_pollIntervalSpin, QOverload<int>::of(&QSpinBox::valueChanged), this, &SettingsWidget::updateEffectiveConfig);
    connect(m_timeoutSpin, QOverload<int>::of(&QSpinBox::valueChanged), this, &SettingsWidget::updateEffectiveConfig);
    connect(m_logLevelCombo, &QComboBox::currentTextChanged, this, &SettingsWidget::updateEffectiveConfig);

    load();
}

void SettingsWidget::load()
{
    RpcSettings rpcStore;
    const RpcEndpointSettings rpc = rpcStore.load();
    const QString rpcUrl = RpcSettings::toDisplayUrl(rpc);

    QSettings settings;
    settings.beginGroup(kGroup);
    const QString network = settings.value("network", "devnet").toString();
    const QString explorerUrl = settings.value("explorerUrl").toString();
    const QString fallbackRpcs = settings.value("fallbackRpcs").toString();
    const int chainId = settings.value("chainId", 1337).toInt();
    const int pollInterval = settings.value("pollIntervalMs", 5000).toInt();
    const int timeoutMs = settings.value("timeoutMs", 8000).toInt();
    const QString logLevel = settings.value("logLevel", "INFO").toString();
    settings.endGroup();

    m_networkCombo->setCurrentIndex(qMax(0, m_networkCombo->findData(network)));
    m_rpcUrlEdit->setText(rpcUrl);
    m_chainIdSpin->setValue(chainId);
    m_explorerUrlEdit->setText(explorerUrl);
    m_fallbackRpcEdit->setPlainText(fallbackRpcs);
    m_pollIntervalSpin->setValue(pollInterval);
    m_timeoutSpin->setValue(timeoutMs);
    m_logLevelCombo->setCurrentText(logLevel);
    m_walletFileLabel->setText(m_walletFilePath);
    m_dataDirLabel->setText(m_dataDir);
    updateEffectiveConfig();
}

bool SettingsWidget::validate(QString& errorMessage) const
{
    const QUrl rpcUrl = QUrl::fromUserInput(m_rpcUrlEdit->text().trimmed());
    if (!rpcUrl.isValid() || rpcUrl.scheme().isEmpty() || rpcUrl.host().isEmpty()) {
        errorMessage = "RPC URL is invalid.";
        return false;
    }
    const QString explorer = m_explorerUrlEdit->text().trimmed();
    if (!explorer.isEmpty()) {
        const QUrl explorerUrl = QUrl::fromUserInput(explorer);
        if (!explorerUrl.isValid() || explorerUrl.scheme().isEmpty() || explorerUrl.host().isEmpty()) {
            errorMessage = "Explorer URL is invalid.";
            return false;
        }
    }
    return true;
}

void SettingsWidget::onSaveClicked()
{
    QString errorMessage;
    if (!validate(errorMessage)) {
        QMessageBox::warning(this, "Invalid Settings", errorMessage);
        return;
    }

    const QUrl rpcUrl = QUrl::fromUserInput(m_rpcUrlEdit->text().trimmed());
    RpcEndpointSettings rpc;
    rpc.scheme = rpcUrl.scheme();
    rpc.host = rpcUrl.host();
    rpc.port = rpcUrl.port(rpcUrl.scheme() == "https" ? 443 : 80);
    rpc.path = rpcUrl.path().isEmpty() ? "/rpc" : rpcUrl.path();
    rpc.username = rpcUrl.userName();
    rpc.password = rpcUrl.password();
    RpcSettings().save(rpc);

    QSettings settings;
    settings.beginGroup(kGroup);
    settings.setValue("network", m_networkCombo->currentData().toString());
    settings.setValue("chainId", m_chainIdSpin->value());
    settings.setValue("explorerUrl", m_explorerUrlEdit->text().trimmed());
    settings.setValue("fallbackRpcs", m_fallbackRpcEdit->toPlainText().trimmed());
    settings.setValue("pollIntervalMs", m_pollIntervalSpin->value());
    settings.setValue("timeoutMs", m_timeoutSpin->value());
    settings.setValue("logLevel", m_logLevelCombo->currentText());
    settings.endGroup();
    settings.sync();

    emit rpcSettingsApplied(rpc, m_explorerUrlEdit->text().trimmed(), m_pollIntervalSpin->value(), m_timeoutSpin->value());
    updateEffectiveConfig();
}

void SettingsWidget::onDefaultsClicked()
{
    m_networkCombo->setCurrentIndex(qMax(0, m_networkCombo->findData("devnet")));
    m_rpcUrlEdit->setText("http://127.0.0.1:8545/rpc");
    m_chainIdSpin->setValue(1337);
    m_explorerUrlEdit->clear();
    m_fallbackRpcEdit->clear();
    m_pollIntervalSpin->setValue(5000);
    m_timeoutSpin->setValue(8000);
    m_logLevelCombo->setCurrentText("INFO");
    updateEffectiveConfig();
}

void SettingsWidget::onExportClicked()
{
    const QString fileName = QFileDialog::getSaveFileName(this, "Export Settings", QDir::home().filePath("animica-wallet-settings.json"), "JSON Files (*.json)");
    if (fileName.isEmpty()) {
        return;
    }
    QJsonDocument doc = QJsonDocument::fromJson(m_effectiveConfigEdit->toPlainText().toUtf8());
    QFile file(fileName);
    if (!file.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
        QMessageBox::warning(this, "Export Failed", "Unable to write the selected settings file.");
        return;
    }
    file.write(doc.toJson(QJsonDocument::Indented));
    file.close();
}

void SettingsWidget::onImportClicked()
{
    const QString fileName = QFileDialog::getOpenFileName(this, "Import Settings", QDir::homePath(), "JSON Files (*.json)");
    if (fileName.isEmpty()) {
        return;
    }
    QFile file(fileName);
    if (!file.open(QIODevice::ReadOnly)) {
        QMessageBox::warning(this, "Import Failed", "Unable to read the selected settings file.");
        return;
    }
    QJsonParseError parseError;
    const QJsonDocument doc = QJsonDocument::fromJson(file.readAll(), &parseError);
    file.close();
    if (parseError.error != QJsonParseError::NoError || !doc.isObject()) {
        QMessageBox::warning(this, "Import Failed", "The selected file is not valid JSON.");
        return;
    }
    const QJsonObject obj = doc.object();
    m_networkCombo->setCurrentIndex(qMax(0, m_networkCombo->findData(obj.value("network").toString("devnet"))));
    m_rpcUrlEdit->setText(obj.value("rpcUrl").toString("http://127.0.0.1:8545/rpc"));
    m_chainIdSpin->setValue(obj.value("chainId").toInt(1337));
    m_explorerUrlEdit->setText(obj.value("explorerUrl").toString());
    m_fallbackRpcEdit->setPlainText(obj.value("fallbackRpcs").toString());
    m_pollIntervalSpin->setValue(obj.value("pollIntervalMs").toInt(5000));
    m_timeoutSpin->setValue(obj.value("timeoutMs").toInt(8000));
    m_logLevelCombo->setCurrentText(obj.value("logLevel").toString("INFO"));
    updateEffectiveConfig();
}

void SettingsWidget::updateEffectiveConfig()
{
    QJsonObject obj;
    obj["network"] = m_networkCombo->currentData().toString();
    obj["rpcUrl"] = m_rpcUrlEdit->text().trimmed();
    obj["chainId"] = m_chainIdSpin->value();
    obj["explorerUrl"] = m_explorerUrlEdit->text().trimmed();
    obj["fallbackRpcs"] = m_fallbackRpcEdit->toPlainText().trimmed();
    obj["pollIntervalMs"] = m_pollIntervalSpin->value();
    obj["timeoutMs"] = m_timeoutSpin->value();
    obj["logLevel"] = m_logLevelCombo->currentText();
    obj["walletFile"] = m_walletFilePath;
    obj["dataDir"] = m_dataDir;
    m_effectiveConfigEdit->setPlainText(QJsonDocument(obj).toJson(QJsonDocument::Indented));
}
