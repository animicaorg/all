#include "AccountsWidget.h"
#include "WalletEngine.h"
#include "BalanceTracker.h"
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QHeaderView>
#include <QMessageBox>
#include <QInputDialog>
#include <QClipboard>
#include <QApplication>

AccountsWidget::AccountsWidget(WalletEngine* engine, QWidget* parent)
    : QWidget(parent)
    , m_engine(engine)
{
    setupUi();
    
    // Connect to engine signals
    connect(m_engine, &WalletEngine::accountAdded, this, &AccountsWidget::handleAccountAdded);
    connect(m_engine, &WalletEngine::accountUpdated, this, &AccountsWidget::handleAccountUpdated);
    connect(m_engine, &WalletEngine::accountRemoved, this, &AccountsWidget::handleAccountRemoved);
    connect(m_engine, &WalletEngine::balanceUpdated, this, &AccountsWidget::handleBalanceUpdated);
}

void AccountsWidget::setupUi()
{
    auto* layout = new QVBoxLayout(this);
    
    // Status label
    m_statusLabel = new QLabel("Accounts", this);
    m_statusLabel->setStyleSheet("font-weight: bold; font-size: 14px;");
    layout->addWidget(m_statusLabel);
    
    // Accounts table
    m_accountTable = new QTableWidget(0, 4, this);
    m_accountTable->setHorizontalHeaderLabels({"", "Label", "Address", "Balance"});
    m_accountTable->horizontalHeader()->setStretchLastSection(true);
    m_accountTable->horizontalHeader()->setSectionResizeMode(0, QHeaderView::Fixed);
    m_accountTable->setColumnWidth(0, 30);  // Star column
    m_accountTable->setSelectionBehavior(QAbstractItemView::SelectRows);
    m_accountTable->setSelectionMode(QAbstractItemView::SingleSelection);
    m_accountTable->setContextMenuPolicy(Qt::CustomContextMenu);
    m_accountTable->setEditTriggers(QAbstractItemView::NoEditTriggers);
    layout->addWidget(m_accountTable);
    
    connect(m_accountTable, &QTableWidget::doubleClicked, 
            this, [this](const QModelIndex& index) { onTableDoubleClicked(index.row(), index.column()); });
    connect(m_accountTable, &QTableWidget::itemSelectionChanged,
            this, &AccountsWidget::onTableSelectionChanged);
    connect(m_accountTable, &QTableWidget::customContextMenuRequested,
            this, &AccountsWidget::onContextMenuRequested);
    
    // Action buttons
    auto* buttonLayout = new QHBoxLayout();
    m_createButton = new QPushButton("Create Account", this);
    m_importButton = new QPushButton("Import", this);
    m_exportButton = new QPushButton("Export", this);
    m_exportButton->setEnabled(false);
    
    buttonLayout->addWidget(m_createButton);
    buttonLayout->addWidget(m_importButton);
    buttonLayout->addWidget(m_exportButton);
    buttonLayout->addStretch();
    layout->addLayout(buttonLayout);
    
    connect(m_createButton, &QPushButton::clicked, this, &AccountsWidget::onCreateClicked);
    connect(m_importButton, &QPushButton::clicked, this, &AccountsWidget::onImportClicked);
    connect(m_exportButton, &QPushButton::clicked, this, &AccountsWidget::onExportClicked);
    
    // Context menu
    m_contextMenu = new QMenu(this);
    m_contextMenu->addAction("Rename", this, &AccountsWidget::onRenameAccount);
    m_contextMenu->addAction("Set as Default", this, &AccountsWidget::onSetDefaultAccount);
    m_contextMenu->addSeparator();
    m_contextMenu->addAction("Copy Address", this, &AccountsWidget::onCopyAddress);
    m_contextMenu->addSeparator();
    m_contextMenu->addAction("Remove", this, &AccountsWidget::onRemoveAccount);
}

void AccountsWidget::refreshAccounts()
{
    m_accountTable->setRowCount(0);
    
    if (!m_engine || m_engine->isLocked()) {
        m_statusLabel->setText("Accounts (Locked)");
        return;
    }
    
    auto accounts = m_engine->listAccounts();
    m_statusLabel->setText(QString("Accounts (%1)").arg(accounts.size()));
    
    for (const auto& account : accounts) {
        int row = m_accountTable->rowCount();
        m_accountTable->insertRow(row);
        updateAccountRow(row, account);
    }
}

void AccountsWidget::updateAccountRow(int row, const WalletAccount& account)
{
    // Star for default account
    auto* starItem = new QTableWidgetItem(account.isDefault ? "★" : "");
    starItem->setData(Qt::UserRole, account.accountId);
    starItem->setTextAlignment(Qt::AlignCenter);
    m_accountTable->setItem(row, 0, starItem);
    
    // Label
    m_accountTable->setItem(row, 1, new QTableWidgetItem(account.label));
    
    // Address (truncated)
    m_accountTable->setItem(row, 2, new QTableWidgetItem(formatAddress(account.address)));
    
    // Balance
    auto balance = m_engine->getBalance(account.address);
    m_accountTable->setItem(row, 3, new QTableWidgetItem(formatBalance(balance.confirmed)));
}

QString AccountsWidget::formatAddress(const QString& address) const
{
    if (address.length() <= 16) {
        return address;
    }
    return address.left(10) + "..." + address.right(6);
}

QString AccountsWidget::formatBalance(quint64 balance) const
{
    // Convert from smallest unit to ANM (1 ANM = 10^18)
    double anm = balance / 1e18;
    return QString::number(anm, 'f', 6) + " ANM";
}

int AccountsWidget::findAccountRow(const QString& accountId) const
{
    for (int i = 0; i < m_accountTable->rowCount(); ++i) {
        auto* item = m_accountTable->item(i, 0);
        if (item && item->data(Qt::UserRole).toString() == accountId) {
            return i;
        }
    }
    return -1;
}

QString AccountsWidget::selectedAccountId() const
{
    auto selected = m_accountTable->selectedItems();
    if (selected.isEmpty()) {
        return QString();
    }
    int row = selected.first()->row();
    auto* item = m_accountTable->item(row, 0);
    return item ? item->data(Qt::UserRole).toString() : QString();
}

void AccountsWidget::onCreateClicked()
{
    emit createAccountRequested();
}

void AccountsWidget::onImportClicked()
{
    emit importAccountRequested();
}

void AccountsWidget::onExportClicked()
{
    QString accountId = selectedAccountId();
    if (!accountId.isEmpty()) {
        emit exportAccountRequested(accountId);
    }
}

void AccountsWidget::onTableDoubleClicked(int row, int column)
{
    Q_UNUSED(column);
    auto* item = m_accountTable->item(row, 0);
    if (item) {
        QString accountId = item->data(Qt::UserRole).toString();
        emit viewAccountDetailsRequested(accountId);
    }
}

void AccountsWidget::onTableSelectionChanged()
{
    bool hasSelection = !m_accountTable->selectedItems().isEmpty();
    m_exportButton->setEnabled(hasSelection);
    
    if (hasSelection) {
        emit accountSelected(selectedAccountId());
    }
}

void AccountsWidget::onContextMenuRequested(const QPoint& pos)
{
    if (m_accountTable->selectedItems().isEmpty()) {
        return;
    }
    m_contextMenu->exec(m_accountTable->viewport()->mapToGlobal(pos));
}

void AccountsWidget::onRenameAccount()
{
    QString accountId = selectedAccountId();
    if (accountId.isEmpty()) return;
    
    auto account = m_engine->getAccount(accountId);
    bool ok;
    QString newLabel = QInputDialog::getText(this, "Rename Account",
                                             "Enter new label:",
                                             QLineEdit::Normal,
                                             account.label, &ok);
    if (ok && !newLabel.isEmpty()) {
        if (!m_engine->renameAccount(accountId, newLabel)) {
            QMessageBox::warning(this, "Error", "Failed to rename account");
        }
    }
}

void AccountsWidget::onSetDefaultAccount()
{
    QString accountId = selectedAccountId();
    if (accountId.isEmpty()) return;
    
    m_engine->setDefaultAccount(accountId);
}

void AccountsWidget::onRemoveAccount()
{
    QString accountId = selectedAccountId();
    if (accountId.isEmpty()) return;
    
    auto account = m_engine->getAccount(accountId);
    auto reply = QMessageBox::question(this, "Remove Account",
                                       QString("Remove account '%1'?\n\nThis cannot be undone unless you have a backup.")
                                       .arg(account.label),
                                       QMessageBox::Yes | QMessageBox::No);
    
    if (reply == QMessageBox::Yes) {
        if (!m_engine->removeAccount(accountId)) {
            QMessageBox::warning(this, "Error", "Failed to remove account");
        }
    }
}

void AccountsWidget::onCopyAddress()
{
    QString accountId = selectedAccountId();
    if (accountId.isEmpty()) return;
    
    auto account = m_engine->getAccount(accountId);
    QApplication::clipboard()->setText(account.address);
}

void AccountsWidget::handleAccountAdded(const WalletAccount& account)
{
    int row = m_accountTable->rowCount();
    m_accountTable->insertRow(row);
    updateAccountRow(row, account);
    m_statusLabel->setText(QString("Accounts (%1)").arg(m_accountTable->rowCount()));
}

void AccountsWidget::handleAccountUpdated(const WalletAccount& account)
{
    int row = findAccountRow(account.accountId);
    if (row >= 0) {
        updateAccountRow(row, account);
    }
}

void AccountsWidget::handleAccountRemoved(const QString& accountId)
{
    int row = findAccountRow(accountId);
    if (row >= 0) {
        m_accountTable->removeRow(row);
        m_statusLabel->setText(QString("Accounts (%1)").arg(m_accountTable->rowCount()));
    }
}

void AccountsWidget::handleBalanceUpdated(const QString& address, const Balance& balance)
{
    Q_UNUSED(balance);
    // Find account by address and update balance
    auto accounts = m_engine->listAccounts();
    for (const auto& account : accounts) {
        if (account.address == address) {
            int row = findAccountRow(account.accountId);
            if (row >= 0) {
                auto bal = m_engine->getBalance(address);
                m_accountTable->item(row, 3)->setText(formatBalance(bal.confirmed));
            }
            break;
        }
    }
}
