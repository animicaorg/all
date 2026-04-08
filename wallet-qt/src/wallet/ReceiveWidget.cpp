#include "ReceiveWidget.h"

#include "BalanceTracker.h"
#include "WalletAccount.h"
#include "WalletEngine.h"

#include <QClipboard>
#include <QDir>
#include <QFileDialog>
#include <QFont>
#include <QFormLayout>
#include <QFrame>
#include <QGuiApplication>
#include <QHBoxLayout>
#include <QMessageBox>
#include <QPixmap>
#include <QRegularExpression>
#include <QRegularExpressionValidator>
#include <QSignalBlocker>
#include <QTimer>
#include <QVBoxLayout>
#include <QtConcurrent/QtConcurrent>

namespace {

constexpr int kQrPreviewSize = 220;
constexpr int kQrDebounceMs = 150;

const char* kPrimaryButtonStyle =
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
    "}";

const char* kSuccessButtonStyle =
    "QPushButton {"
    "  background-color: #2E7D32;"
    "  color: white;"
    "  border: none;"
    "  border-radius: 4px;"
    "  padding: 8px 16px;"
    "  font-weight: bold;"
    "}";

} // namespace

ReceiveWidget::ReceiveWidget(WalletEngine* walletEngine,
                             QWidget* parent,
                             std::shared_ptr<ReceiveQrService> qrService)
    : QWidget(parent)
    , m_walletEngine(walletEngine)
    , m_qrService(qrService ? std::move(qrService) : std::make_shared<ReceiveQrService>())
    , m_accountCombo(nullptr)
    , m_addressLabel(nullptr)
    , m_qrCodeLabel(nullptr)
    , m_qrStatusLabel(nullptr)
    , m_copyButton(nullptr)
    , m_saveQrButton(nullptr)
    , m_amountEdit(nullptr)
    , m_messageEdit(nullptr)
    , m_balanceLabel(nullptr)
    , m_qrUpdateTimer(new QTimer(this))
    , m_qrWatcher(new QFutureWatcher<ReceiveQrResult>(this))
    , m_qrRequestedRevision(0)
    , m_qrActiveRevision(0)
    , m_qrGenerationPending(false)
{
    Q_ASSERT(m_walletEngine);

    m_qrUpdateTimer->setSingleShot(true);
    m_qrUpdateTimer->setInterval(kQrDebounceMs);
    connect(m_qrUpdateTimer, &QTimer::timeout, this, &ReceiveWidget::startQrGeneration);
    connect(m_qrWatcher, &QFutureWatcher<ReceiveQrResult>::finished,
            this, &ReceiveWidget::onQrGenerationFinished);

    setupUi();

    BalanceTracker* tracker = m_walletEngine->balanceTracker();
    if (tracker) {
        connect(tracker, &BalanceTracker::balanceUpdated,
                this, &ReceiveWidget::onBalanceUpdated);
    }

    connect(m_walletEngine, &WalletEngine::accountAdded, this, &ReceiveWidget::refresh);
    connect(m_walletEngine, &WalletEngine::accountUpdated, this, &ReceiveWidget::refresh);
    connect(m_walletEngine, &WalletEngine::accountRemoved, this, &ReceiveWidget::refresh);
    connect(m_walletEngine, &WalletEngine::walletLocked, this, &ReceiveWidget::refresh);
    connect(m_walletEngine, &WalletEngine::walletUnlocked, this, &ReceiveWidget::refresh);
}

ReceiveWidget::~ReceiveWidget()
{
}

void ReceiveWidget::setupUi()
{
    QVBoxLayout* mainLayout = new QVBoxLayout(this);
    mainLayout->setContentsMargins(20, 20, 20, 20);
    mainLayout->setSpacing(15);

    QLabel* titleLabel = new QLabel("Receive Funds", this);
    QFont titleFont = titleLabel->font();
    titleFont.setPointSize(16);
    titleFont.setBold(true);
    titleLabel->setFont(titleFont);
    mainLayout->addWidget(titleLabel);

    QFrame* line = new QFrame(this);
    line->setFrameShape(QFrame::HLine);
    line->setFrameShadow(QFrame::Sunken);
    mainLayout->addWidget(line);

    QHBoxLayout* accountLayout = new QHBoxLayout();
    accountLayout->addWidget(new QLabel("Account:", this));

    m_accountCombo = new QComboBox(this);
    m_accountCombo->setObjectName("receiveAccountCombo");
    m_accountCombo->setMinimumWidth(300);
    connect(m_accountCombo, SIGNAL(currentIndexChanged(int)),
            this, SLOT(onAccountChanged(int)));
    accountLayout->addWidget(m_accountCombo, 1);
    mainLayout->addLayout(accountLayout);

    m_balanceLabel = new QLabel(this);
    m_balanceLabel->setObjectName("receiveBalanceLabel");
    m_balanceLabel->setStyleSheet(
        "QLabel { color: #2E7D32; font-weight: bold; padding-left: 60px; }");
    mainLayout->addWidget(m_balanceLabel);

    mainLayout->addSpacing(10);

    QLabel* addrTitleLabel = new QLabel("Your Address:", this);
    QFont sectionTitleFont = addrTitleLabel->font();
    sectionTitleFont.setBold(true);
    addrTitleLabel->setFont(sectionTitleFont);
    mainLayout->addWidget(addrTitleLabel);

    QFrame* addressFrame = new QFrame(this);
    addressFrame->setFrameStyle(QFrame::Box | QFrame::Sunken);
    addressFrame->setStyleSheet(
        "QFrame { background-color: #F5F5F5; border: 1px solid #CCCCCC; border-radius: 4px; }");
    QVBoxLayout* addressLayout = new QVBoxLayout(addressFrame);
    addressLayout->setContentsMargins(10, 10, 10, 10);

    m_addressLabel = new QLabel(this);
    m_addressLabel->setObjectName("receiveAddressLabel");
    m_addressLabel->setWordWrap(true);
    m_addressLabel->setTextInteractionFlags(Qt::TextSelectableByMouse);
    QFont monoFont("Courier", 10);
    m_addressLabel->setFont(monoFont);
    m_addressLabel->setStyleSheet("QLabel { background-color: transparent; }");
    addressLayout->addWidget(m_addressLabel);

    m_copyButton = new QPushButton("Copy to Clipboard", this);
    m_copyButton->setObjectName("receiveCopyButton");
    m_copyButton->setStyleSheet(kPrimaryButtonStyle);
    connect(m_copyButton, &QPushButton::clicked, this, &ReceiveWidget::onCopyClicked);
    addressLayout->addWidget(m_copyButton);

    mainLayout->addWidget(addressFrame);
    mainLayout->addSpacing(10);

    QLabel* qrTitleLabel = new QLabel("QR Code:", this);
    qrTitleLabel->setFont(sectionTitleFont);
    mainLayout->addWidget(qrTitleLabel);

    m_qrCodeLabel = new QLabel(this);
    m_qrCodeLabel->setObjectName("receiveQrCodeLabel");
    m_qrCodeLabel->setAlignment(Qt::AlignCenter);
    m_qrCodeLabel->setWordWrap(true);
    m_qrCodeLabel->setMinimumSize(kQrPreviewSize, kQrPreviewSize);
    m_qrCodeLabel->setMaximumSize(kQrPreviewSize, kQrPreviewSize);
    m_qrCodeLabel->setStyleSheet(
        "QLabel {"
        "  border: 1px solid #CCCCCC;"
        "  background-color: white;"
        "  padding: 12px;"
        "}");

    QHBoxLayout* qrLayout = new QHBoxLayout();
    qrLayout->addStretch();
    qrLayout->addWidget(m_qrCodeLabel);
    qrLayout->addStretch();
    mainLayout->addLayout(qrLayout);

    QHBoxLayout* qrActionsLayout = new QHBoxLayout();
    qrActionsLayout->addStretch();
    m_saveQrButton = new QPushButton("Save QR as PNG", this);
    m_saveQrButton->setObjectName("receiveSaveQrButton");
    m_saveQrButton->setEnabled(false);
    connect(m_saveQrButton, &QPushButton::clicked, this, &ReceiveWidget::onSaveQrClicked);
    qrActionsLayout->addWidget(m_saveQrButton);
    qrActionsLayout->addStretch();
    mainLayout->addLayout(qrActionsLayout);

    m_qrStatusLabel = new QLabel(this);
    m_qrStatusLabel->setObjectName("receiveQrStatusLabel");
    m_qrStatusLabel->setWordWrap(true);
    m_qrStatusLabel->setAlignment(Qt::AlignCenter);
    m_qrStatusLabel->setStyleSheet("QLabel { color: #555555; }");
    mainLayout->addWidget(m_qrStatusLabel);

    mainLayout->addSpacing(10);

    QFormLayout* requestLayout = new QFormLayout();
    requestLayout->setFieldGrowthPolicy(QFormLayout::ExpandingFieldsGrow);

    m_amountEdit = new QLineEdit(this);
    m_amountEdit->setObjectName("receiveAmountEdit");
    m_amountEdit->setPlaceholderText("Optional ANM amount");
    m_amountEdit->setValidator(new QRegularExpressionValidator(
        QRegularExpression(QStringLiteral("\\d*(?:\\.\\d{0,9})?")), m_amountEdit));
    connect(m_amountEdit, &QLineEdit::textChanged, this, [this](const QString&) {
        scheduleQrGeneration();
    });
    requestLayout->addRow("Amount (ANM):", m_amountEdit);

    m_messageEdit = new QLineEdit(this);
    m_messageEdit->setObjectName("receiveMessageEdit");
    m_messageEdit->setPlaceholderText("Optional memo included in QR");
    connect(m_messageEdit, &QLineEdit::textChanged, this, [this](const QString&) {
        scheduleQrGeneration();
    });
    requestLayout->addRow("Message:", m_messageEdit);

    mainLayout->addLayout(requestLayout);
    mainLayout->addStretch();

    updateAccounts();
}

void ReceiveWidget::refresh()
{
    updateAccounts();
}

void ReceiveWidget::updateAccounts()
{
    const QString previousAccountId = m_accountCombo->currentData().toString();
    QSignalBlocker blocker(m_accountCombo);
    m_accountCombo->clear();

    if (m_walletEngine->isLocked()) {
        m_accountCombo->addItem("(Wallet Locked)");
        m_accountCombo->setEnabled(false);
        m_addressLabel->clear();
        m_balanceLabel->clear();
        blocker.unblock();
        scheduleQrGeneration();
        return;
    }

    QList<WalletAccount> accounts = m_walletEngine->listAccounts();
    if (accounts.isEmpty()) {
        m_accountCombo->addItem("(No Accounts)");
        m_accountCombo->setEnabled(false);
        m_addressLabel->clear();
        m_balanceLabel->clear();
        blocker.unblock();
        scheduleQrGeneration();
        return;
    }

    int selectedIndex = 0;
    for (int i = 0; i < accounts.size(); ++i) {
        const WalletAccount& account = accounts.at(i);
        QString displayText = account.label;
        if (account.isDefault) {
            displayText += " (Default)";
        }
        m_accountCombo->addItem(displayText, account.accountId);
        if (!previousAccountId.isEmpty() && account.accountId == previousAccountId) {
            selectedIndex = i;
        }
    }

    m_accountCombo->setEnabled(true);
    m_accountCombo->setCurrentIndex(selectedIndex);
    blocker.unblock();
    onAccountChanged(selectedIndex);
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
    if (m_walletEngine->isLocked() || !m_accountCombo->isEnabled() || m_accountCombo->count() == 0) {
        m_addressLabel->clear();
        scheduleQrGeneration();
        return;
    }

    const QString accountId = m_accountCombo->currentData().toString();
    if (accountId.isEmpty()) {
        m_addressLabel->clear();
        scheduleQrGeneration();
        return;
    }

    const QList<WalletAccount> accounts = m_walletEngine->listAccounts();
    for (const WalletAccount& account : accounts) {
        if (account.accountId == accountId) {
            m_addressLabel->setText(account.address);
            scheduleQrGeneration();
            return;
        }
    }

    m_addressLabel->clear();
    scheduleQrGeneration();
}

void ReceiveWidget::updateBalance()
{
    if (m_walletEngine->isLocked() || !m_accountCombo->isEnabled() || m_accountCombo->count() == 0) {
        m_balanceLabel->clear();
        return;
    }

    const QString accountId = m_accountCombo->currentData().toString();
    if (accountId.isEmpty()) {
        m_balanceLabel->clear();
        return;
    }

    const QList<WalletAccount> accounts = m_walletEngine->listAccounts();
    for (const WalletAccount& account : accounts) {
        if (account.accountId == accountId) {
            BalanceTracker* tracker = m_walletEngine->balanceTracker();
            if (tracker) {
                const Balance balance = tracker->getBalance(account.address);
                m_balanceLabel->setText("Balance: " + formatBalance(balance.confirmed));
            } else {
                m_balanceLabel->setText("Balance: N/A");
            }
            return;
        }
    }

    m_balanceLabel->clear();
}

void ReceiveWidget::onCopyClicked()
{
    const QString address = m_addressLabel->text();
    if (address.isEmpty()) {
        return;
    }

    QGuiApplication::clipboard()->setText(address);

    m_copyButton->setText("✓ Copied!");
    m_copyButton->setStyleSheet(kSuccessButtonStyle);

    QTimer::singleShot(2000, this, [this]() {
        m_copyButton->setText("Copy to Clipboard");
        m_copyButton->setStyleSheet(kPrimaryButtonStyle);
    });
}

void ReceiveWidget::onSaveQrClicked()
{
    if (m_currentQrImage.isNull()) {
        return;
    }

    QString defaultName = "animica-receive-qr.png";
    if (!m_addressLabel->text().isEmpty()) {
        defaultName = QString("animica-%1.png").arg(m_addressLabel->text().left(12));
    }

    const QString targetPath = QFileDialog::getSaveFileName(
        this,
        "Save Receive QR",
        QDir::home().filePath(defaultName),
        "PNG Images (*.png)"
    );
    if (targetPath.isEmpty()) {
        return;
    }

    QString errorMessage;
    if (!ReceiveQrService::savePng(m_currentQrImage, targetPath, &errorMessage)) {
        QMessageBox::warning(this, "Save QR Failed", errorMessage);
        return;
    }

    m_saveQrButton->setText("✓ Saved!");
    QTimer::singleShot(2000, this, [this]() {
        m_saveQrButton->setText("Save QR as PNG");
    });
}

void ReceiveWidget::onBalanceUpdated(const QString& address, const Balance& balance)
{
    Q_UNUSED(balance);

    if (m_walletEngine->isLocked() || !m_accountCombo->isEnabled() || m_accountCombo->count() == 0) {
        return;
    }

    const QString accountId = m_accountCombo->currentData().toString();
    if (accountId.isEmpty()) {
        return;
    }

    const QList<WalletAccount> accounts = m_walletEngine->listAccounts();
    for (const WalletAccount& account : accounts) {
        if (account.accountId == accountId && account.address == address) {
            updateBalance();
            break;
        }
    }
}

void ReceiveWidget::scheduleQrGeneration()
{
    ++m_qrRequestedRevision;
    m_qrGenerationPending = true;
    m_saveQrButton->setEnabled(false);

    if (m_qrWatcher->isRunning()) {
        return;
    }
    m_qrUpdateTimer->start();
}

void ReceiveWidget::startQrGeneration()
{
    if (m_qrWatcher->isRunning() || !m_qrGenerationPending) {
        return;
    }

    ReceiveQrRequest request;
    request.address = m_addressLabel->text();
    request.amount = m_amountEdit->text();
    request.message = m_messageEdit->text();
    request.pixelSize = 512;

    m_qrGenerationPending = false;
    m_qrActiveRevision = m_qrRequestedRevision;

    if (!request.address.trimmed().isEmpty()) {
        m_qrCodeLabel->setPixmap(QPixmap());
        m_qrCodeLabel->setText("Generating QR...");
        m_qrStatusLabel->setText("Rendering an animica payment QR for the selected wallet.");
    }

    const std::shared_ptr<ReceiveQrService> qrService = m_qrService;
    m_qrWatcher->setFuture(QtConcurrent::run([qrService, request]() {
        return qrService->generate(request);
    }));
}

void ReceiveWidget::onQrGenerationFinished()
{
    const ReceiveQrResult result = m_qrWatcher->result();
    const bool isLatestResult = (m_qrActiveRevision == m_qrRequestedRevision);
    if (isLatestResult) {
        applyQrResult(result);
    }

    if (m_qrGenerationPending) {
        m_qrUpdateTimer->start();
    }
}

void ReceiveWidget::applyQrResult(const ReceiveQrResult& result)
{
    m_currentQrImage = QImage();
    m_currentQrPayload.clear();
    m_saveQrButton->setEnabled(false);

    if (result.isSuccess()) {
        m_currentQrImage = result.image;
        m_currentQrPayload = result.payload;

        const QPixmap qrPixmap = QPixmap::fromImage(result.image).scaled(
            m_qrCodeLabel->size(),
            Qt::KeepAspectRatio,
            Qt::FastTransformation);
        m_qrCodeLabel->setPixmap(qrPixmap);
        m_qrCodeLabel->setText(QString());
        m_qrCodeLabel->setToolTip(result.payload);
        m_qrStatusLabel->setText("QR ready. Scan it or save it as a PNG from this screen.");
        m_qrStatusLabel->setToolTip(result.payload);
        m_saveQrButton->setEnabled(true);
        return;
    }

    m_qrCodeLabel->setPixmap(QPixmap());
    m_qrCodeLabel->setText(result.errorSummary);
    m_qrCodeLabel->setToolTip(QString());
    m_qrStatusLabel->setToolTip(QString());

    if (result.errorDetails.isEmpty()) {
        m_qrStatusLabel->setText(result.errorSummary);
    } else {
        m_qrStatusLabel->setText(result.errorDetails);
    }
}

QString ReceiveWidget::formatBalance(qint64 wei) const
{
    if (wei == 0) {
        return "0.0 ANM";
    }

    const double anm = static_cast<double>(wei) / 1e9;
    QString formatted;
    if (anm >= 1.0) {
        formatted = QString::number(anm, 'f', 6);
    } else {
        formatted = QString::number(anm, 'f', 8);
    }

    while (formatted.contains('.') && (formatted.endsWith('0') || formatted.endsWith('.'))) {
        if (formatted.endsWith('.')) {
            formatted.chop(1);
            break;
        }
        formatted.chop(1);
    }

    return formatted + " ANM";
}
