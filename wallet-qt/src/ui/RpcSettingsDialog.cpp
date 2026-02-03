#include "RpcSettingsDialog.h"
#include "../rpc/AnimicaRpcClient.h"
#include "../rpc/RpcReply.h"
#include <QVBoxLayout>
#include <QFormLayout>
#include <QHBoxLayout>
#include <QComboBox>
#include <QLineEdit>
#include <QSpinBox>
#include <QLabel>
#include <QPushButton>
#include <QDialogButtonBox>

RpcSettingsDialog::RpcSettingsDialog(QWidget* parent)
    : QDialog(parent)
    , m_testClient(new AnimicaRpcClient(this))
    , m_testReply(nullptr)
{
    setWindowTitle("RPC Settings");
    setMinimumWidth(420);

    auto* layout = new QVBoxLayout(this);
    auto* formLayout = new QFormLayout();

    m_schemeCombo = new QComboBox(this);
    m_schemeCombo->addItem("http");
    m_schemeCombo->addItem("https");
    formLayout->addRow("Scheme:", m_schemeCombo);

    m_hostEdit = new QLineEdit(this);
    formLayout->addRow("Host/IP:", m_hostEdit);

    m_portSpin = new QSpinBox(this);
    m_portSpin->setRange(1, 65535);
    formLayout->addRow("Port:", m_portSpin);

    m_pathEdit = new QLineEdit(this);
    m_pathEdit->setPlaceholderText("/rpc");
    formLayout->addRow("Path:", m_pathEdit);

    m_usernameEdit = new QLineEdit(this);
    formLayout->addRow("Username (optional):", m_usernameEdit);

    m_passwordEdit = new QLineEdit(this);
    m_passwordEdit->setEchoMode(QLineEdit::Password);
    formLayout->addRow("Password (optional):", m_passwordEdit);

    layout->addLayout(formLayout);

    m_urlPreviewLabel = new QLabel(this);
    m_urlPreviewLabel->setStyleSheet("color: #555;");
    layout->addWidget(m_urlPreviewLabel);

    m_securityWarningLabel = new QLabel(this);
    m_securityWarningLabel->setStyleSheet("color: #b45309;");
    m_securityWarningLabel->setWordWrap(true);
    layout->addWidget(m_securityWarningLabel);

    m_statusLabel = new QLabel(this);
    m_statusLabel->setStyleSheet("color: #666;");
    layout->addWidget(m_statusLabel);

    auto* buttonLayout = new QHBoxLayout();
    m_testButton = new QPushButton("Test Connection", this);
    m_saveButton = new QPushButton("Save", this);
    m_resetButton = new QPushButton("Reset to Default", this);
    buttonLayout->addWidget(m_testButton);
    buttonLayout->addStretch();
    buttonLayout->addWidget(m_resetButton);
    buttonLayout->addWidget(m_saveButton);
    layout->addLayout(buttonLayout);

    connect(m_testButton, &QPushButton::clicked, this, &RpcSettingsDialog::onTestConnection);
    connect(m_saveButton, &QPushButton::clicked, this, &RpcSettingsDialog::onSave);
    connect(m_resetButton, &QPushButton::clicked, this, &RpcSettingsDialog::onResetDefaults);
    connect(m_schemeCombo, &QComboBox::currentTextChanged, this, &RpcSettingsDialog::updateUrlPreview);
    connect(m_hostEdit, &QLineEdit::textChanged, this, &RpcSettingsDialog::updateUrlPreview);
    connect(m_portSpin, QOverload<int>::of(&QSpinBox::valueChanged), this, &RpcSettingsDialog::updateUrlPreview);
    connect(m_pathEdit, &QLineEdit::textChanged, this, &RpcSettingsDialog::updateUrlPreview);

    loadFromSettings();
    updateUrlPreview();
}

void RpcSettingsDialog::loadFromSettings()
{
    RpcEndpointSettings settings = m_settingsStore.load();
    m_schemeCombo->setCurrentText(settings.scheme);
    m_hostEdit->setText(settings.host);
    m_portSpin->setValue(settings.port);
    m_pathEdit->setText(settings.path);
    m_usernameEdit->setText(settings.username);
    m_passwordEdit->setText(settings.password);
}

RpcEndpointSettings RpcSettingsDialog::gatherSettings() const
{
    RpcEndpointSettings settings;
    settings.scheme = m_schemeCombo->currentText().trimmed();
    settings.host = m_hostEdit->text().trimmed();
    settings.port = m_portSpin->value();
    settings.path = m_pathEdit->text().trimmed();
    if (settings.path.isEmpty()) {
        settings.path = "/rpc";
    }
    settings.username = m_usernameEdit->text().trimmed();
    settings.password = m_passwordEdit->text();
    return settings;
}

void RpcSettingsDialog::updateUrlPreview()
{
    RpcEndpointSettings settings = gatherSettings();
    QString displayUrl = RpcSettings::toDisplayUrl(settings);
    m_urlPreviewLabel->setText("RPC URL: " + displayUrl);
    updateSecurityWarning();
}

void RpcSettingsDialog::updateSecurityWarning()
{
    if (m_schemeCombo->currentText().trimmed().toLower() == "http") {
        m_securityWarningLabel->setText("Warning: HTTP connections are not encrypted. Use HTTPS if available.");
    } else {
        m_securityWarningLabel->clear();
    }
}

void RpcSettingsDialog::setStatusMessage(const QString& message, const QString& color)
{
    m_statusLabel->setText(message);
    m_statusLabel->setStyleSheet(QString("color: %1;").arg(color));
}

void RpcSettingsDialog::onTestConnection()
{
    if (m_testReply && !m_testReply->isFinished()) {
        return;
    }

    RpcEndpointSettings settings = gatherSettings();
    QUrl url = RpcSettings::toUrl(settings);
    m_testClient->setEndpoint(url.toString());
    m_testClient->setTimeout(8000);
    m_testClient->setRetryPolicy(2, 500);

    m_testButton->setEnabled(false);
    setStatusMessage("Testing connection...", "#666");

    m_testReply = m_testClient->ping();
    connect(m_testReply, &RpcReply::finished, this, [this]() {
        if (m_testReply->error() == QNetworkReply::NoError) {
            setStatusMessage("Connection successful (pong received).", "#15803d");
        } else {
            setStatusMessage(QString("Connection failed: %1").arg(m_testReply->errorString()), "#b91c1c");
        }
        m_testReply->deleteLater();
        m_testReply = nullptr;
        m_testButton->setEnabled(true);
    });
}

void RpcSettingsDialog::onSave()
{
    RpcEndpointSettings settings = gatherSettings();
    m_settingsStore.save(settings);
    emit settingsSaved(settings);
    accept();
}

void RpcSettingsDialog::onResetDefaults()
{
    RpcEndpointSettings defaults = m_settingsStore.defaults();
    m_schemeCombo->setCurrentText(defaults.scheme);
    m_hostEdit->setText(defaults.host);
    m_portSpin->setValue(defaults.port);
    m_pathEdit->setText(defaults.path);
    m_usernameEdit->clear();
    m_passwordEdit->clear();
    updateUrlPreview();
    setStatusMessage("Reset to default endpoint.", "#666");
}
