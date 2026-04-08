#include "ReceiveWidget.h"
#include "WalletEngine.h"
#include "WalletAccount.h"
#include "BalanceTracker.h"
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QFormLayout>
#include <QFrame>
#include <QFont>
#include <QGuiApplication>
#include <QClipboard>
#include <QTimer>
#include <QPainter>
#include <QPixmap>
#include <QDebug>

ReceiveWidget::ReceiveWidget(WalletEngine* walletEngine, QWidget* parent)
    : QWidget(parent)
    , m_walletEngine(walletEngine)
    , m_accountCombo(nullptr)
    , m_addressLabel(nullptr)
    , m_qrCodeLabel(nullptr)
    , m_copyButton(nullptr)
    , m_noteEdit(nullptr)
    , m_balanceLabel(nullptr)
{
    Q_ASSERT(m_walletEngine);
    
    setupUi();
    
    // Connect to balance tracker for updates
    BalanceTracker* tracker = m_walletEngine->balanceTracker();
    if (tracker) {
        connect(tracker, &BalanceTracker::balanceUpdated,
                this, &ReceiveWidget::onBalanceUpdated);
    }
}

ReceiveWidget::~ReceiveWidget()
{
}

void ReceiveWidget::setupUi()
{
    QVBoxLayout* mainLayout = new QVBoxLayout(this);
    mainLayout->setContentsMargins(20, 20, 20, 20);
    mainLayout->setSpacing(15);
    
    // Title
    QLabel* titleLabel = new QLabel("Receive Funds", this);
    QFont titleFont = titleLabel->font();
    titleFont.setPointSize(16);
    titleFont.setBold(true);
    titleLabel->setFont(titleFont);
    mainLayout->addWidget(titleLabel);
    
    // Horizontal line separator
    QFrame* line = new QFrame(this);
    line->setFrameShape(QFrame::HLine);
    line->setFrameShadow(QFrame::Sunken);
    mainLayout->addWidget(line);
    
    // Account selector
    QHBoxLayout* accountLayout = new QHBoxLayout();
    accountLayout->addWidget(new QLabel("Account:", this));
    m_accountCombo = new QComboBox(this);
    m_accountCombo->setMinimumWidth(300);
    connect(m_accountCombo, SIGNAL(currentIndexChanged(int)),
            this, SLOT(onAccountChanged(int)));
    accountLayout->addWidget(m_accountCombo, 1);
    mainLayout->addLayout(accountLayout);
    
    // Balance display
    m_balanceLabel = new QLabel(this);
    m_balanceLabel->setStyleSheet("QLabel { color: #2E7D32; font-weight: bold; padding-left: 60px; }");
    mainLayout->addWidget(m_balanceLabel);
    
    mainLayout->addSpacing(10);
    
    // Address display
    QLabel* addrTitleLabel = new QLabel("Your Address:", this);
    QFont addrTitleFont = addrTitleLabel->font();
    addrTitleFont.setBold(true);
    addrTitleLabel->setFont(addrTitleFont);
    mainLayout->addWidget(addrTitleLabel);
    
    QFrame* addressFrame = new QFrame(this);
    addressFrame->setFrameStyle(QFrame::Box | QFrame::Sunken);
    addressFrame->setStyleSheet("QFrame { background-color: #F5F5F5; border: 1px solid #CCCCCC; border-radius: 4px; }");
    QVBoxLayout* addressLayout = new QVBoxLayout(addressFrame);
    addressLayout->setContentsMargins(10, 10, 10, 10);
    
    m_addressLabel = new QLabel(this);
    m_addressLabel->setWordWrap(true);
    m_addressLabel->setTextInteractionFlags(Qt::TextSelectableByMouse);
    QFont monoFont("Courier", 10);
    m_addressLabel->setFont(monoFont);
    m_addressLabel->setStyleSheet("QLabel { background-color: transparent; }");
    addressLayout->addWidget(m_addressLabel);
    
    m_copyButton = new QPushButton("Copy to Clipboard", this);
    m_copyButton->setStyleSheet(
        "QPushButton {"
        "  background-color: #1976D2;"
        "  color: white;"
        "  border: none;"
        "  border-radius: 4px;"
        "  padding: 8px 16px;"
        "  font-weight: bold;"
        "}"
        "QPushButton:hover {"
        "  background-color: #1565C0;"
        "}"
        "QPushButton:pressed {"
        "  background-color: #0D47A1;"
        "}"
    );
    connect(m_copyButton, &QPushButton::clicked, this, &ReceiveWidget::onCopyClicked);
    addressLayout->addWidget(m_copyButton);
    
    mainLayout->addWidget(addressFrame);
    
    mainLayout->addSpacing(10);
    
    // QR Code
    QLabel* qrTitleLabel = new QLabel("QR Code:", this);
    qrTitleLabel->setFont(addrTitleFont);
    mainLayout->addWidget(qrTitleLabel);
    
    m_qrCodeLabel = new QLabel(this);
    m_qrCodeLabel->setAlignment(Qt::AlignCenter);
    m_qrCodeLabel->setMinimumSize(200, 200);
    m_qrCodeLabel->setMaximumSize(200, 200);
    m_qrCodeLabel->setStyleSheet("QLabel { border: 1px solid #CCCCCC; background-color: white; }");
    
    QHBoxLayout* qrLayout = new QHBoxLayout();
    qrLayout->addStretch();
    qrLayout->addWidget(m_qrCodeLabel);
    qrLayout->addStretch();
    mainLayout->addLayout(qrLayout);
    
    mainLayout->addSpacing(10);
    
    // Payment note
    QFormLayout* noteLayout = new QFormLayout();
    noteLayout->setFieldGrowthPolicy(QFormLayout::ExpandingFieldsGrow);
    m_noteEdit = new QLineEdit(this);
    m_noteEdit->setPlaceholderText("(local label, not sent)");
    noteLayout->addRow("Payment Note:", m_noteEdit);
    mainLayout->addLayout(noteLayout);
    
    mainLayout->addSpacing(10);
    
    mainLayout->addStretch();
    
    // Load accounts
    updateAccounts();
}

void ReceiveWidget::refresh()
{
    updateAccounts();
}

void ReceiveWidget::updateAccounts()
{
    m_accountCombo->clear();
    
    if (m_walletEngine->isLocked()) {
        m_accountCombo->addItem("(Wallet Locked)");
        m_accountCombo->setEnabled(false);
        m_addressLabel->setText("");
        m_balanceLabel->setText("");
        generateQRCode();
        return;
    }
    
    m_accountCombo->setEnabled(true);
    
    QList<WalletAccount> accounts = m_walletEngine->listAccounts();
    
    if (accounts.isEmpty()) {
        m_accountCombo->addItem("(No Accounts)");
        m_addressLabel->setText("");
        m_balanceLabel->setText("");
        generateQRCode();
        return;
    }
    
    for (const WalletAccount& account : accounts) {
        QString displayText = account.label;
        if (account.isDefault) {
            displayText += " (Default)";
        }
        m_accountCombo->addItem(displayText, account.accountId);
    }
    
    // Select first account
    if (m_accountCombo->count() > 0) {
        m_accountCombo->setCurrentIndex(0);
        onAccountChanged(0);
    }
}

void ReceiveWidget::onAccountChanged(int index)
{
    if (index < 0 || m_accountCombo->count() == 0) {
        return;
    }
    
    updateAddress();
    updateBalance();
}

void ReceiveWidget::updateAddress()
{
    if (m_walletEngine->isLocked() || m_accountCombo->count() == 0) {
        m_addressLabel->setText("");
        generateQRCode();
        return;
    }
    
    QString accountId = m_accountCombo->currentData().toString();
    if (accountId.isEmpty()) {
        m_addressLabel->setText("");
        generateQRCode();
        return;
    }
    
    QList<WalletAccount> accounts = m_walletEngine->listAccounts();
    for (const WalletAccount& account : accounts) {
        if (account.accountId == accountId) {
            m_addressLabel->setText(account.address);
            generateQRCode();
            return;
        }
    }
    
    m_addressLabel->setText("");
    generateQRCode();
}

void ReceiveWidget::updateBalance()
{
    if (m_walletEngine->isLocked() || m_accountCombo->count() == 0) {
        m_balanceLabel->setText("");
        return;
    }
    
    QString accountId = m_accountCombo->currentData().toString();
    if (accountId.isEmpty()) {
        m_balanceLabel->setText("");
        return;
    }
    
    QList<WalletAccount> accounts = m_walletEngine->listAccounts();
    for (const WalletAccount& account : accounts) {
        if (account.accountId == accountId) {
            BalanceTracker* tracker = m_walletEngine->balanceTracker();
            if (tracker) {
                Balance balance = tracker->getBalance(account.address);
                m_balanceLabel->setText("Balance: " + formatBalance(balance.confirmed));
            } else {
                m_balanceLabel->setText("Balance: N/A");
            }
            return;
        }
    }
    
    m_balanceLabel->setText("");
}

void ReceiveWidget::onCopyClicked()
{
    QString address = m_addressLabel->text();
    if (address.isEmpty()) {
        return;
    }
    
    QClipboard* clipboard = QGuiApplication::clipboard();
    clipboard->setText(address);
    
    // Show feedback
    m_copyButton->setText("✓ Copied!");
    m_copyButton->setStyleSheet(
        "QPushButton {"
        "  background-color: #2E7D32;"
        "  color: white;"
        "  border: none;"
        "  border-radius: 4px;"
        "  padding: 8px 16px;"
        "  font-weight: bold;"
        "}"
    );
    
    QTimer::singleShot(2000, [this]() {
        m_copyButton->setText("Copy to Clipboard");
        m_copyButton->setStyleSheet(
            "QPushButton {"
            "  background-color: #1976D2;"
            "  color: white;"
            "  border: none;"
            "  border-radius: 4px;"
            "  padding: 8px 16px;"
            "  font-weight: bold;"
            "}"
            "QPushButton:hover {"
            "  background-color: #1565C0;"
            "}"
            "QPushButton:pressed {"
            "  background-color: #0D47A1;"
            "}"
        );
    });
}

void ReceiveWidget::onBalanceUpdated(const QString& address, const Balance& balance)
{
    // Check if this is the currently selected account
    if (m_walletEngine->isLocked() || m_accountCombo->count() == 0) {
        return;
    }
    
    QString accountId = m_accountCombo->currentData().toString();
    if (accountId.isEmpty()) {
        return;
    }
    
    QList<WalletAccount> accounts = m_walletEngine->listAccounts();
    for (const WalletAccount& account : accounts) {
        if (account.accountId == accountId && account.address == address) {
            updateBalance();
            break;
        }
    }
}

void ReceiveWidget::generateQRCode()
{
    QString address = m_addressLabel->text();
    
    if (address.isEmpty()) {
        // Show placeholder for empty state
        QPixmap placeholder(200, 200);
        placeholder.fill(Qt::white);
        
        QPainter painter(&placeholder);
        painter.setPen(Qt::gray);
        painter.drawRect(0, 0, 199, 199);
        painter.drawText(placeholder.rect(), Qt::AlignCenter, "No Address");
        
        m_qrCodeLabel->setPixmap(placeholder);
        return;
    }
    
    QPixmap notice(200, 200);
    notice.fill(Qt::white);

    QPainter painter(&notice);
    painter.setPen(Qt::darkGray);
    painter.drawRect(0, 0, 199, 199);
    painter.setFont(QFont("Arial", 9));
    painter.drawText(notice.rect(), Qt::AlignCenter, "QR unavailable\nin this build");

    m_qrCodeLabel->setPixmap(notice);
}

QString ReceiveWidget::formatBalance(qint64 wei) const
{
    if (wei == 0) {
        return "0.0 ANM";
    }

    double anm = static_cast<double>(wei) / 1e9;
    QString formatted;
    if (anm >= 1.0) {
        formatted = QString::number(anm, 'f', 6);
    } else {
        formatted = QString::number(anm, 'f', 8);
    }
    
    // Remove trailing zeros
    while (formatted.contains('.') && (formatted.endsWith('0') || formatted.endsWith('.'))) {
        if (formatted.endsWith('.')) {
            formatted.chop(1);
            break;
        }
        formatted.chop(1);
    }
    
    return formatted + " ANM";
}
