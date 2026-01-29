#include "UnlockDialog.h"
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QFormLayout>
#include <QLabel>
#include <QKeyEvent>
#include <QApplication>
#include <QStyle>

UnlockDialog::UnlockDialog(QWidget* parent)
    : QDialog(parent)
    , m_failedAttempts(0)
{
    setupUi();
    
    // Install event filter to detect Caps Lock
    m_passwordEdit->installEventFilter(this);
    
    setWindowTitle("Unlock Wallet");
    setModal(true);
    setFixedWidth(400);
}

QString UnlockDialog::password() const
{
    return m_passwordEdit->text();
}

bool UnlockDialog::rememberForSession() const
{
    return m_rememberSessionCheck->isChecked();
}

int UnlockDialog::autoLockMinutes() const
{
    return m_autoLockSpinBox->value();
}

int UnlockDialog::recordFailedAttempt()
{
    m_failedAttempts++;
    
    if (m_failedAttempts <= MAX_FREE_ATTEMPTS) {
        return 0;
    }
    
    // Calculate exponential backoff
    if (m_failedAttempts == 6) return 5000;    // 5 seconds
    if (m_failedAttempts == 7) return 10000;   // 10 seconds
    return 30000;  // 30 seconds for 8+
}

void UnlockDialog::resetFailedAttempts()
{
    m_failedAttempts = 0;
    m_errorLabel->clear();
}

void UnlockDialog::showError(const QString& message)
{
    m_errorLabel->setText(message);
    m_errorLabel->setStyleSheet("QLabel { color: red; }");
}

void UnlockDialog::keyPressEvent(QKeyEvent* event)
{
    checkCapsLock();
    QDialog::keyPressEvent(event);
}

bool UnlockDialog::eventFilter(QObject* watched, QEvent* event)
{
    if (watched == m_passwordEdit) {
        if (event->type() == QEvent::KeyPress || event->type() == QEvent::FocusIn) {
            checkCapsLock();
        }
    }
    return QDialog::eventFilter(watched, event);
}

void UnlockDialog::onShowPasswordToggled(bool checked)
{
    m_passwordEdit->setEchoMode(checked ? QLineEdit::Normal : QLineEdit::Password);
}

void UnlockDialog::onPasswordChanged(const QString& text)
{
    // Enable unlock button only if password is not empty
    m_unlockButton->setEnabled(!text.isEmpty());
    
    // Clear error when user starts typing again
    if (!text.isEmpty() && !m_errorLabel->text().isEmpty()) {
        m_errorLabel->clear();
    }
}

void UnlockDialog::setupUi()
{
    auto* layout = new QVBoxLayout(this);
    
    // Title label
    auto* titleLabel = new QLabel("Enter password to unlock wallet", this);
    QFont titleFont = titleLabel->font();
    titleFont.setPointSize(titleFont.pointSize() + 2);
    titleFont.setBold(true);
    titleLabel->setFont(titleFont);
    titleLabel->setAlignment(Qt::AlignCenter);
    layout->addWidget(titleLabel);
    
    layout->addSpacing(10);
    
    // Password input
    auto* formLayout = new QFormLayout();
    
    m_passwordEdit = new QLineEdit(this);
    m_passwordEdit->setEchoMode(QLineEdit::Password);
    m_passwordEdit->setPlaceholderText("Enter password");
    connect(m_passwordEdit, &QLineEdit::textChanged, this, &UnlockDialog::onPasswordChanged);
    connect(m_passwordEdit, &QLineEdit::returnPressed, this, &QDialog::accept);
    formLayout->addRow("Password:", m_passwordEdit);
    
    layout->addLayout(formLayout);
    
    // Show password checkbox
    m_showPasswordCheck = new QCheckBox("Show password", this);
    connect(m_showPasswordCheck, &QCheckBox::toggled, this, &UnlockDialog::onShowPasswordToggled);
    layout->addWidget(m_showPasswordCheck);
    
    // Caps Lock warning
    m_capsLockLabel = new QLabel(this);
    m_capsLockLabel->setStyleSheet("QLabel { color: orange; }");
    m_capsLockLabel->hide();
    layout->addWidget(m_capsLockLabel);
    
    // Error label
    m_errorLabel = new QLabel(this);
    m_errorLabel->setWordWrap(true);
    layout->addWidget(m_errorLabel);
    
    layout->addSpacing(10);
    
    // Options
    m_rememberSessionCheck = new QCheckBox("Keep wallet unlocked for this session", this);
    m_rememberSessionCheck->setToolTip("Wallet will remain unlocked until you close the application or manually lock it");
    layout->addWidget(m_rememberSessionCheck);
    
    auto* autoLockLayout = new QHBoxLayout();
    autoLockLayout->addWidget(new QLabel("Auto-lock after:", this));
    m_autoLockSpinBox = new QSpinBox(this);
    m_autoLockSpinBox->setRange(0, 120);  // 0-120 minutes
    m_autoLockSpinBox->setValue(15);       // Default 15 minutes
    m_autoLockSpinBox->setSuffix(" min");
    m_autoLockSpinBox->setSpecialValueText("Never");
    m_autoLockSpinBox->setToolTip("Automatically lock wallet after period of inactivity (0 = never)");
    autoLockLayout->addWidget(m_autoLockSpinBox);
    autoLockLayout->addStretch();
    layout->addLayout(autoLockLayout);
    
    layout->addSpacing(20);
    
    // Buttons
    auto* buttonLayout = new QHBoxLayout();
    buttonLayout->addStretch();
    
    m_cancelButton = new QPushButton("Cancel", this);
    connect(m_cancelButton, &QPushButton::clicked, this, &QDialog::reject);
    buttonLayout->addWidget(m_cancelButton);
    
    m_unlockButton = new QPushButton("Unlock", this);
    m_unlockButton->setDefault(true);
    m_unlockButton->setEnabled(false);  // Disabled until password entered
    connect(m_unlockButton, &QPushButton::clicked, this, &QDialog::accept);
    buttonLayout->addWidget(m_unlockButton);
    
    layout->addLayout(buttonLayout);
    
    // Set focus to password field
    m_passwordEdit->setFocus();
}

void UnlockDialog::checkCapsLock()
{
    // Check if Caps Lock is on
    bool capsLockOn = QApplication::queryKeyboardModifiers() & Qt::ShiftModifier;
    
    if (capsLockOn) {
        m_capsLockLabel->setText("⚠ Caps Lock is ON");
        m_capsLockLabel->show();
    } else {
        m_capsLockLabel->hide();
    }
}
