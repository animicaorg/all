#include "SettingsWidget.h"

#include "../rpc/RpcSettings.h"
#include "WalletSecuritySettings.h"

#include <QCheckBox>
#include <QDir>
#include <QFile>
#include <QFileDialog>
#include <QFormLayout>
#include <QGroupBox>
#include <QHBoxLayout>
#include <QInputDialog>
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

    auto* endpointGroup = new QGroupBox("Hosted Network", this);
    auto* endpointLayout = new QFormLayout(endpointGroup);

    m_networkValueLabel = new QLabel("Animica Mainnet", this);
    m_networkValueLabel->setTextInteractionFlags(Qt::TextSelectableByMouse);
    endpointLayout->addRow("Network:", m_networkValueLabel);

    m_rpcUrlValueLabel = new QLabel(RpcSettings::canonicalRpcUrl(), this);
    m_rpcUrlValueLabel->setTextInteractionFlags(Qt::TextSelectableByMouse);
    endpointLayout->addRow("RPC Endpoint:", m_rpcUrlValueLabel);
    layout->addWidget(endpointGroup);

    auto* runtimeGroup = new QGroupBox("Wallet Preferences", this);
    auto* runtimeLayout = new QFormLayout(runtimeGroup);

    m_explorerUrlEdit = new QLineEdit(this);
    m_explorerUrlEdit->setPlaceholderText("Optional explorer base URL");
    runtimeLayout->addRow("Explorer URL:", m_explorerUrlEdit);

    m_pollIntervalSpin = new QSpinBox(this);
    m_pollIntervalSpin->setRange(1000, 600000);
    m_pollIntervalSpin->setSingleStep(1000);
    runtimeLayout->addRow("Balance Polling (ms):", m_pollIntervalSpin);

    m_timeoutSpin = new QSpinBox(this);
    m_timeoutSpin->setRange(1000, 120000);
    m_timeoutSpin->setSingleStep(1000);
    runtimeLayout->addRow("RPC Timeout (ms):", m_timeoutSpin);

    m_walletFileLabel = new QLabel(this);
    m_walletFileLabel->setTextInteractionFlags(Qt::TextSelectableByMouse);
    runtimeLayout->addRow("Wallet File:", m_walletFileLabel);

    m_dataDirLabel = new QLabel(this);
    m_dataDirLabel->setTextInteractionFlags(Qt::TextSelectableByMouse);
    runtimeLayout->addRow("Data Directory:", m_dataDirLabel);
    layout->addWidget(runtimeGroup);

    auto* securityGroup = new QGroupBox("Security", this);
    auto* securityLayout = new QVBoxLayout(securityGroup);

    m_transferPasswordStatusLabel = new QLabel(this);
    m_transferPasswordStatusLabel->setStyleSheet("color: #555;");
    securityLayout->addWidget(m_transferPasswordStatusLabel);

    auto* passwordButtonRow = new QHBoxLayout();
    m_setTransferPasswordButton = new QPushButton("Set Transfer Password", this);
    m_clearTransferPasswordButton = new QPushButton("Clear Transfer Password", this);
    passwordButtonRow->addWidget(m_setTransferPasswordButton);
    passwordButtonRow->addWidget(m_clearTransferPasswordButton);
    passwordButtonRow->addStretch();
    securityLayout->addLayout(passwordButtonRow);

    m_requireTransferPasswordCheck = new QCheckBox("Require transfer password for sending ANM", this);
    securityLayout->addWidget(m_requireTransferPasswordCheck);

    m_encryptWalletCheck = new QCheckBox("Encrypt wallet setting (lock wallet access with transfer password)", this);
    securityLayout->addWidget(m_encryptWalletCheck);

    auto* securityNoteLabel = new QLabel(
        "This setting protects wallet access in the app and keeps the canonical wallets.json format unchanged.",
        this
    );
    securityNoteLabel->setWordWrap(true);
    securityNoteLabel->setStyleSheet("color: #666; font-size: 11px;");
    securityLayout->addWidget(securityNoteLabel);
    layout->addWidget(securityGroup);

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
    connect(m_setTransferPasswordButton, &QPushButton::clicked, this, &SettingsWidget::onSetTransferPasswordClicked);
    connect(m_clearTransferPasswordButton, &QPushButton::clicked, this, &SettingsWidget::onClearTransferPasswordClicked);
    connect(m_explorerUrlEdit, &QLineEdit::textChanged, this, &SettingsWidget::updateEffectiveConfig);
    connect(m_pollIntervalSpin, QOverload<int>::of(&QSpinBox::valueChanged), this, &SettingsWidget::updateEffectiveConfig);
    connect(m_timeoutSpin, QOverload<int>::of(&QSpinBox::valueChanged), this, &SettingsWidget::updateEffectiveConfig);
    connect(m_requireTransferPasswordCheck, &QCheckBox::toggled, this, &SettingsWidget::updateEffectiveConfig);
    connect(m_encryptWalletCheck, &QCheckBox::toggled, this, &SettingsWidget::updateEffectiveConfig);

    load();
}

void SettingsWidget::load()
{
    QSettings settings;
    settings.beginGroup(kGroup);
    const QString explorerUrl = settings.value("explorerUrl").toString();
    const int pollInterval = settings.value("pollIntervalMs", 5000).toInt();
    const int timeoutMs = settings.value("timeoutMs", 8000).toInt();
    settings.endGroup();

    m_explorerUrlEdit->setText(explorerUrl);
    m_pollIntervalSpin->setValue(pollInterval);
    m_timeoutSpin->setValue(timeoutMs);
    m_walletFileLabel->setText(m_walletFilePath);
    m_dataDirLabel->setText(m_dataDir);
    m_requireTransferPasswordCheck->setChecked(WalletSecuritySettings::requireTransferPasswordForSend());
    m_encryptWalletCheck->setChecked(WalletSecuritySettings::walletEncryptionEnabled());
    refreshSecurityState();
    updateEffectiveConfig();
}

bool SettingsWidget::validate(QString& errorMessage) const
{
    const QString explorer = m_explorerUrlEdit->text().trimmed();
    if (!explorer.isEmpty()) {
        const QUrl explorerUrl = QUrl::fromUserInput(explorer);
        if (!explorerUrl.isValid() || explorerUrl.scheme().isEmpty() || explorerUrl.host().isEmpty()) {
            errorMessage = "Explorer URL is invalid.";
            return false;
        }
    }

    if ((m_requireTransferPasswordCheck->isChecked() || m_encryptWalletCheck->isChecked())
        && !WalletSecuritySettings::hasTransferPassword()) {
        errorMessage = QString(
            "Set a transfer password before enabling send protection or wallet encryption."
        );
        return false;
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

    QSettings settings;
    settings.beginGroup(kGroup);
    settings.setValue("network", RpcSettings::canonicalNetwork());
    settings.setValue("chainId", RpcSettings::canonicalChainId());
    settings.setValue("explorerUrl", m_explorerUrlEdit->text().trimmed());
    settings.setValue("pollIntervalMs", m_pollIntervalSpin->value());
    settings.setValue("timeoutMs", m_timeoutSpin->value());
    settings.endGroup();
    settings.sync();

    WalletSecuritySettings::setRequireTransferPasswordForSend(m_requireTransferPasswordCheck->isChecked());
    WalletSecuritySettings::setWalletEncryptionEnabled(m_encryptWalletCheck->isChecked());

    emit settingsApplied(m_explorerUrlEdit->text().trimmed(), m_pollIntervalSpin->value(), m_timeoutSpin->value());
    refreshSecurityState();
    updateEffectiveConfig();
}

void SettingsWidget::onDefaultsClicked()
{
    m_explorerUrlEdit->clear();
    m_pollIntervalSpin->setValue(5000);
    m_timeoutSpin->setValue(8000);
    m_requireTransferPasswordCheck->setChecked(false);
    m_encryptWalletCheck->setChecked(false);
    updateEffectiveConfig();
}

void SettingsWidget::onExportClicked()
{
    const QString fileName = QFileDialog::getSaveFileName(
        this,
        "Export Settings",
        QDir::home().filePath("animica-wallet-settings.json"),
        "JSON Files (*.json)"
    );
    if (fileName.isEmpty()) {
        return;
    }

    const QJsonDocument doc = QJsonDocument::fromJson(m_effectiveConfigEdit->toPlainText().toUtf8());
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
    const QString fileName = QFileDialog::getOpenFileName(
        this,
        "Import Settings",
        QDir::homePath(),
        "JSON Files (*.json)"
    );
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
    m_explorerUrlEdit->setText(obj.value("explorerUrl").toString());
    m_pollIntervalSpin->setValue(obj.value("pollIntervalMs").toInt(5000));
    m_timeoutSpin->setValue(obj.value("timeoutMs").toInt(8000));
    m_requireTransferPasswordCheck->setChecked(obj.value("requireTransferPasswordForSend").toBool(false));
    m_encryptWalletCheck->setChecked(obj.value("walletEncryptionEnabled").toBool(false));
    refreshSecurityState();
    updateEffectiveConfig();
}

void SettingsWidget::onSetTransferPasswordClicked()
{
    bool ok = false;
    const QString password = QInputDialog::getText(
        this,
        "Set Transfer Password",
        QString("Enter a transfer password (%1+ characters):").arg(WalletSecuritySettings::kMinPasswordLength),
        QLineEdit::Password,
        QString(),
        &ok
    );
    if (!ok) {
        return;
    }
    if (password.size() < WalletSecuritySettings::kMinPasswordLength) {
        QMessageBox::warning(
            this,
            "Weak Password",
            QString("Transfer password must be at least %1 characters.")
                .arg(WalletSecuritySettings::kMinPasswordLength)
        );
        return;
    }

    const QString confirmation = QInputDialog::getText(
        this,
        "Confirm Transfer Password",
        "Re-enter the transfer password:",
        QLineEdit::Password,
        QString(),
        &ok
    );
    if (!ok) {
        return;
    }
    if (password != confirmation) {
        QMessageBox::warning(this, "Password Mismatch", "The two password entries do not match.");
        return;
    }

    if (!WalletSecuritySettings::setTransferPassword(password)) {
        QMessageBox::warning(this, "Password Error", "Failed to save transfer password.");
        return;
    }

    QMessageBox::information(this, "Password Saved", "Transfer password has been updated.");
    refreshSecurityState();
    updateEffectiveConfig();
}

void SettingsWidget::onClearTransferPasswordClicked()
{
    if (!WalletSecuritySettings::hasTransferPassword()) {
        refreshSecurityState();
        return;
    }

    bool ok = false;
    const QString currentPassword = QInputDialog::getText(
        this,
        "Clear Transfer Password",
        "Enter current transfer password:",
        QLineEdit::Password,
        QString(),
        &ok
    );
    if (!ok) {
        return;
    }

    if (!WalletSecuritySettings::clearTransferPassword(currentPassword)) {
        QMessageBox::warning(this, "Authorization Failed", "Current transfer password is incorrect.");
        return;
    }

    m_requireTransferPasswordCheck->setChecked(false);
    m_encryptWalletCheck->setChecked(false);
    QMessageBox::information(this, "Password Cleared", "Transfer password and security locks were removed.");
    refreshSecurityState();
    updateEffectiveConfig();
}

void SettingsWidget::refreshSecurityState()
{
    const bool hasPassword = WalletSecuritySettings::hasTransferPassword();
    m_transferPasswordStatusLabel->setText(hasPassword
        ? "Transfer password: configured"
        : "Transfer password: not configured");
    m_clearTransferPasswordButton->setEnabled(hasPassword);

    if (!hasPassword) {
        m_requireTransferPasswordCheck->setChecked(false);
        m_encryptWalletCheck->setChecked(false);
    }
}

void SettingsWidget::updateEffectiveConfig()
{
    QJsonObject obj;
    obj["network"] = RpcSettings::canonicalNetwork();
    obj["chainId"] = RpcSettings::canonicalChainId();
    obj["rpcUrl"] = RpcSettings::canonicalRpcUrl();
    obj["explorerUrl"] = m_explorerUrlEdit->text().trimmed();
    obj["pollIntervalMs"] = m_pollIntervalSpin->value();
    obj["timeoutMs"] = m_timeoutSpin->value();
    obj["requireTransferPasswordForSend"] = m_requireTransferPasswordCheck->isChecked();
    obj["walletEncryptionEnabled"] = m_encryptWalletCheck->isChecked();
    obj["walletFile"] = m_walletFilePath;
    obj["dataDir"] = m_dataDir;
    m_effectiveConfigEdit->setPlainText(QJsonDocument(obj).toJson(QJsonDocument::Indented));
}
