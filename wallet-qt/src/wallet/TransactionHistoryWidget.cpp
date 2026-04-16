#include "TransactionHistoryWidget.h"

#include "WalletEngine.h"

#include <QtConcurrent/QtConcurrentRun>

#include <QCheckBox>
#include <QComboBox>
#include <QDateEdit>
#include <QDateTime>
#include <QDialog>
#include <QDialogButtonBox>
#include <QDir>
#include <QFile>
#include <QFileDialog>
#include <QFormLayout>
#include <QHeaderView>
#include <QHBoxLayout>
#include <QJsonDocument>
#include <QMessageBox>
#include <QPushButton>
#include <QTableWidget>
#include <QTextEdit>
#include <QTime>
#include <QVBoxLayout>
#include <QLineEdit>

TransactionHistoryWidget::TransactionHistoryWidget(WalletEngine* engine, QWidget* parent)
    : QWidget(parent)
    , m_engine(engine)
    , m_refreshWatcher(new QFutureWatcher<QJsonObject>(this))
    , m_detailsWatcher(new QFutureWatcher<QJsonObject>(this))
{
    auto* layout = new QVBoxLayout(this);

    auto* filters = new QFormLayout();
    m_walletFilter = new QComboBox(this);
    m_directionFilter = new QComboBox(this);
    m_directionFilter->addItem("All", "all");
    m_directionFilter->addItem("Incoming", "incoming");
    m_directionFilter->addItem("Outgoing", "outgoing");
    m_directionFilter->addItem("Self", "self");
    m_statusFilter = new QComboBox(this);
    m_statusFilter->addItem("All", "all");
    m_statusFilter->addItems({"broadcast", "mempool_accepted", "pending", "confirmed", "SUCCESS", "FAILED"});
    m_searchEdit = new QLineEdit(this);
    m_searchEdit->setPlaceholderText("Search by hash or address");
    filters->addRow("Wallet:", m_walletFilter);
    filters->addRow("Direction:", m_directionFilter);
    filters->addRow("Status:", m_statusFilter);
    filters->addRow("Search:", m_searchEdit);
    layout->addLayout(filters);

    auto* dateRow = new QHBoxLayout();
    m_useDateFilter = new QCheckBox("Use date range", this);
    m_fromDateEdit = new QDateEdit(QDate::currentDate().addMonths(-1), this);
    m_fromDateEdit->setCalendarPopup(true);
    m_toDateEdit = new QDateEdit(QDate::currentDate(), this);
    m_toDateEdit->setCalendarPopup(true);
    dateRow->addWidget(m_useDateFilter);
    dateRow->addWidget(m_fromDateEdit);
    dateRow->addWidget(m_toDateEdit);
    dateRow->addStretch();
    layout->addLayout(dateRow);

    auto* buttons = new QHBoxLayout();
    m_refreshButton = new QPushButton("Refresh", this);
    m_exportJsonButton = new QPushButton("Export JSON", this);
    m_exportCsvButton = new QPushButton("Export CSV", this);
    buttons->addWidget(m_refreshButton);
    buttons->addWidget(m_exportJsonButton);
    buttons->addWidget(m_exportCsvButton);
    buttons->addStretch();
    layout->addLayout(buttons);

    m_table = new QTableWidget(0, 8, this);
    m_table->setHorizontalHeaderLabels({"Time", "Hash", "Direction", "Amount", "Fee", "Status", "Block", "Counterparty"});
    m_table->horizontalHeader()->setStretchLastSection(true);
    m_table->setSelectionBehavior(QAbstractItemView::SelectRows);
    m_table->setEditTriggers(QAbstractItemView::NoEditTriggers);
    layout->addWidget(m_table);

    connect(m_refreshButton, &QPushButton::clicked, this, &TransactionHistoryWidget::refresh);
    connect(m_exportJsonButton, &QPushButton::clicked, this, &TransactionHistoryWidget::exportJson);
    connect(m_exportCsvButton, &QPushButton::clicked, this, &TransactionHistoryWidget::exportCsv);
    connect(m_refreshWatcher, &QFutureWatcher<QJsonObject>::finished, this, &TransactionHistoryWidget::handleRefreshFinished);
    connect(m_detailsWatcher, &QFutureWatcher<QJsonObject>::finished, this, &TransactionHistoryWidget::handleDetailsFinished);
    connect(m_table, &QTableWidget::cellDoubleClicked, this, [this](int, int) { onDetailsRequested(); });
    connect(m_engine, &WalletEngine::accountAdded, this, [this](const WalletAccount&) { populateWalletFilter(); });
    connect(m_engine, &WalletEngine::accountRemoved, this, [this](const QString&) { populateWalletFilter(); });

    populateWalletFilter();
}

TransactionHistoryWidget::~TransactionHistoryWidget()
{
    if (m_refreshWatcher->isRunning()) {
        m_refreshWatcher->future().waitForFinished();
    }
    if (m_detailsWatcher->isRunning()) {
        m_detailsWatcher->future().waitForFinished();
    }
}

void TransactionHistoryWidget::populateWalletFilter()
{
    const QString current = m_walletFilter->currentData().toString();
    m_walletFilter->clear();
    m_walletFilter->addItem("All Wallets", "");
    for (const WalletAccount& account : m_engine->listAccounts()) {
        m_walletFilter->addItem(account.label, account.accountId);
    }
    const int index = m_walletFilter->findData(current);
    if (index >= 0) {
        m_walletFilter->setCurrentIndex(index);
    }
}

void TransactionHistoryWidget::refresh()
{
    if (m_refreshWatcher->isRunning()) {
        return;
    }
    QJsonObject filters;
    if (!m_walletFilter->currentData().toString().isEmpty()) {
        filters["wallet_id"] = m_walletFilter->currentData().toString();
    }
    filters["direction"] = m_directionFilter->currentData().toString();
    filters["status"] = m_statusFilter->currentData().toString();
    filters["search"] = m_searchEdit->text().trimmed();
    if (m_useDateFilter->isChecked()) {
        filters["start_time"] = QDateTime(m_fromDateEdit->date(), QTime(0, 0), Qt::UTC).toString(Qt::ISODate);
        filters["end_time"] = QDateTime(m_toDateEdit->date(), QTime(23, 59, 59), Qt::UTC).toString(Qt::ISODate);
    }
    m_refreshButton->setEnabled(false);
    WalletEngine* engine = m_engine;
    m_refreshWatcher->setFuture(QtConcurrent::run([engine, filters]() {
        return engine->fetchTransactionHistory(filters);
    }));
}

void TransactionHistoryWidget::handleRefreshFinished()
{
    m_refreshButton->setEnabled(true);
    const QJsonObject result = m_refreshWatcher->future().result();
    m_items = result.value("items").toArray();
    renderTable();
}

void TransactionHistoryWidget::renderTable()
{
    m_table->setRowCount(0);
    for (const QJsonValue& value : m_items) {
        if (!value.isObject()) {
            continue;
        }
        const QJsonObject item = value.toObject();
        const int row = m_table->rowCount();
        m_table->insertRow(row);
        const QString hash = item.value("hash").toString();
        const QString amount = item.value("amount").toVariant().toString();
        const QString fee = item.value("fee").toVariant().toString();
        m_table->setItem(row, 0, new QTableWidgetItem(item.value("time").toString()));
        auto* hashItem = new QTableWidgetItem(hash);
        hashItem->setData(Qt::UserRole, hash);
        m_table->setItem(row, 1, hashItem);
        m_table->setItem(row, 2, new QTableWidgetItem(item.value("direction").toString()));
        m_table->setItem(row, 3, new QTableWidgetItem(amount));
        m_table->setItem(row, 4, new QTableWidgetItem(fee));
        m_table->setItem(row, 5, new QTableWidgetItem(item.value("status").toString()));
        m_table->setItem(row, 6, new QTableWidgetItem(item.value("block_height").toVariant().toString()));
        m_table->setItem(row, 7, new QTableWidgetItem(item.value("counterparty").toString()));
    }
}

void TransactionHistoryWidget::onDetailsRequested()
{
    const auto selected = m_table->selectedItems();
    if (selected.isEmpty() || m_detailsWatcher->isRunning()) {
        return;
    }
    const QString txHash = m_table->item(selected.first()->row(), 1)->data(Qt::UserRole).toString();
    WalletEngine* engine = m_engine;
    m_detailsWatcher->setFuture(QtConcurrent::run([engine, txHash]() {
        return engine->transactionDetails(txHash);
    }));
}

void TransactionHistoryWidget::handleDetailsFinished()
{
    const QJsonObject details = m_detailsWatcher->future().result();
    QDialog dialog(this);
    dialog.setWindowTitle("Transaction Details");
    dialog.resize(700, 500);
    auto* layout = new QVBoxLayout(&dialog);
    auto* viewer = new QTextEdit(&dialog);
    viewer->setReadOnly(true);
    viewer->setPlainText(QString::fromUtf8(QJsonDocument(details).toJson(QJsonDocument::Indented)));
    layout->addWidget(viewer);
    auto* buttons = new QDialogButtonBox(QDialogButtonBox::Close, &dialog);
    connect(buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
    connect(buttons, &QDialogButtonBox::accepted, &dialog, &QDialog::accept);
    layout->addWidget(buttons);
    dialog.exec();
}

void TransactionHistoryWidget::exportJson()
{
    const QString fileName = QFileDialog::getSaveFileName(this, "Export History JSON", QDir::home().filePath("wallet-history.json"), "JSON Files (*.json)");
    if (fileName.isEmpty()) {
        return;
    }
    QFile file(fileName);
    if (!file.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
        QMessageBox::warning(this, "Export Failed", "Unable to write the selected JSON file.");
        return;
    }
    file.write(QJsonDocument(m_items).toJson(QJsonDocument::Indented));
    file.close();
}

void TransactionHistoryWidget::exportCsv()
{
    const QString fileName = QFileDialog::getSaveFileName(this, "Export History CSV", QDir::home().filePath("wallet-history.csv"), "CSV Files (*.csv)");
    if (fileName.isEmpty()) {
        return;
    }
    QFile file(fileName);
    if (!file.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
        QMessageBox::warning(this, "Export Failed", "Unable to write the selected CSV file.");
        return;
    }
    file.write("time,hash,direction,amount,fee,status,block_height,counterparty\n");
    for (const QJsonValue& value : m_items) {
        const QJsonObject item = value.toObject();
        const QString line = QString("\"%1\",\"%2\",\"%3\",\"%4\",\"%5\",\"%6\",\"%7\",\"%8\"\n")
                                 .arg(item.value("time").toString(),
                                      item.value("hash").toString(),
                                      item.value("direction").toString(),
                                      item.value("amount").toVariant().toString(),
                                      item.value("fee").toVariant().toString(),
                                      item.value("status").toString(),
                                      item.value("block_height").toVariant().toString(),
                                      item.value("counterparty").toString());
        file.write(line.toUtf8());
    }
    file.close();
}
