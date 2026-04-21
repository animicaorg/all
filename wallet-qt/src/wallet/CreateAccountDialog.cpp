#include "CreateAccountDialog.h"
#include "WalletSecuritySettings.h"
#include "WalletEngine.h"
#include <QVBoxLayout>
#include <QFormLayout>
#include <QDialogButtonBox>
#include <QInputDialog>
#include <QJsonArray>
#include <QJsonObject>
#include <QMessageBox>
#include <QTimer>

CreateAccountDialog::CreateAccountDialog(WalletEngine* engine, QWidget* parent)
    : QDialog(parent)
    , m_engine(engine)
{
    setupUi();
    setWindowTitle("Create New Account");
    setMinimumWidth(450);
}

void CreateAccountDialog::setupUi()
{
    auto* layout = new QVBoxLayout(this);
    
    // Form for inputs
    auto* formLayout = new QFormLayout();
    
    // Label input
    m_labelEdit = new QLineEdit(this);
    m_labelEdit->setText(suggestLabel());
    m_labelEdit->setPlaceholderText("e.g., Main Account, Savings, ...");
    formLayout->addRow("Account Label:", m_labelEdit);
    
    // Algorithm selection
    m_algorithmCombo = new QComboBox(this);
    const QJsonArray algorithms = m_engine->supportedAlgorithms();
    for (const QJsonValue& value : algorithms) {
        const QJsonObject item = value.toObject();
        const QString name = item["name"].toString();
        const QString display = item["display_name"].toString(name);
        const int algId = item["alg_id"].toInt();
        QString label = display;
        if (name == "dilithium3") {
            label += " (Recommended)";
        }
        m_algorithmCombo->addItem(label, algId);
    }
    if (m_algorithmCombo->count() == 0) {
        m_algorithmCombo->addItem("dilithium3", 0x1001);
    }
    formLayout->addRow("Algorithm:", m_algorithmCombo);
    
    layout->addLayout(formLayout);
    
    // Progress section
    m_progressLabel = new QLabel("Ready to create account", this);
    m_progressBar = new QProgressBar(this);
    m_progressBar->setRange(0, 0);  // Indeterminate
    m_progressBar->setVisible(false);
    layout->addWidget(m_progressLabel);
    layout->addWidget(m_progressBar);
    
    // Address display
    m_addressLabel = new QLabel(this);
    m_addressLabel->setWordWrap(true);
    m_addressLabel->setStyleSheet("QLabel { background-color: #f0f0f0; padding: 8px; border-radius: 4px; }");
    m_addressLabel->setVisible(false);
    layout->addWidget(m_addressLabel);
    
    layout->addSpacing(10);
    
    // Buttons
    auto* buttonBox = new QDialogButtonBox(this);
    m_createButton = buttonBox->addButton("Create", QDialogButtonBox::AcceptRole);
    m_closeButton = buttonBox->addButton("Cancel", QDialogButtonBox::RejectRole);
    
    connect(m_createButton, &QPushButton::clicked, this, &CreateAccountDialog::onCreateClicked);
    connect(m_closeButton, &QPushButton::clicked, this, &QDialog::reject);
    
    layout->addWidget(buttonBox);
}

QString CreateAccountDialog::suggestLabel() const
{
    if (!m_engine) {
        return "Account 1";
    }
    
    auto accounts = m_engine->listAccounts();
    int nextNum = accounts.size() + 1;
    
    // Check if suggested name exists
    QString suggestion;
    bool found;
    do {
        suggestion = QString("Account %1").arg(nextNum);
        found = false;
        for (const auto& acc : accounts) {
            if (acc.label == suggestion) {
                found = true;
                nextNum++;
                break;
            }
        }
    } while (found);
    
    return suggestion;
}

QString CreateAccountDialog::accountLabel() const
{
    return m_labelEdit->text().trimmed();
}

int CreateAccountDialog::algorithmId() const
{
    return m_algorithmCombo->currentData().toInt();
}

void CreateAccountDialog::onCreateClicked()
{
    if (!m_engine->isLoaded()) {
        QMessageBox::warning(this, "Wallet Unavailable",
                             m_engine->lastError().trimmed().isEmpty()
                                 ? QStringLiteral("The wallet store is unavailable. The application could not open or create wallets.json.")
                                 : m_engine->lastError().trimmed());
        return;
    }

    QString password;
    if (m_engine->isLocked() && WalletSecuritySettings::walletEncryptionEnabled()) {
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
            return;
        }
        if (!WalletSecuritySettings::verifyTransferPassword(entered)) {
            QMessageBox::warning(this, "Unlock Failed", "Wallet password is incorrect.");
            return;
        }
        password = entered;
    }

    if (m_engine->isLocked() && !m_engine->unlockWallet(password)) {
        QMessageBox::warning(
            this,
            "Unlock Failed",
            m_engine->lastError().trimmed().isEmpty()
                ? QStringLiteral("The wallet store could not be unlocked.")
                : QString("The wallet store could not be unlocked.\n\n%1").arg(m_engine->lastError().trimmed())
        );
        return;
    }

    QString label = accountLabel();
    
    if (label.isEmpty()) {
        QMessageBox::warning(this, "Error", "Account label cannot be empty");
        return;
    }
    
    // Check for duplicate label
    auto accounts = m_engine->listAccounts();
    for (const auto& acc : accounts) {
        if (acc.label == label) {
            QMessageBox::warning(this, "Error", "An account with this label already exists");
            return;
        }
    }
    
    // Disable inputs during creation
    m_labelEdit->setEnabled(false);
    m_algorithmCombo->setEnabled(false);
    m_createButton->setEnabled(false);
    m_progressLabel->setText("Generating cryptographic keys...");
    m_progressBar->setVisible(true);
    
    // Create account (may take a few seconds for key generation)
    // Run in event loop to avoid blocking UI
    QTimer::singleShot(100, this, &CreateAccountDialog::onAccountCreated);
}

void CreateAccountDialog::onAccountCreated()
{
    auto account = m_engine->createAccount(accountLabel(), algorithmId());
    
    m_progressBar->setVisible(false);
    
    if (account.address.isEmpty()) {
        m_progressLabel->setText("Failed to create account");
        m_labelEdit->setEnabled(true);
        m_algorithmCombo->setEnabled(true);
        m_createButton->setEnabled(true);
        QMessageBox::critical(
            this,
            "Error",
            m_engine->lastError().trimmed().isEmpty()
                ? QStringLiteral("Failed to create account.")
                : QString("Failed to create account.\n\n%1").arg(m_engine->lastError().trimmed())
        );
        return;
    }
    
    // Success!
    m_generatedAddress = account.address;
    m_progressLabel->setText("Account created successfully!");
    m_addressLabel->setText(QString("<b>Address:</b><br>%1").arg(account.address));
    m_addressLabel->setVisible(true);
    
    // Change button to "Close"
    m_createButton->setVisible(false);
    m_closeButton->setText("Close");
    
    // Auto-accept after showing address
    QTimer::singleShot(1500, this, &QDialog::accept);
}
