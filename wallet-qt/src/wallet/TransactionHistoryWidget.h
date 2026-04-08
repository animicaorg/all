#ifndef TRANSACTIONHISTORYWIDGET_H
#define TRANSACTIONHISTORYWIDGET_H

#include <QFutureWatcher>
#include <QJsonArray>
#include <QWidget>

class WalletEngine;
class QCheckBox;
class QComboBox;
class QDateEdit;
class QLineEdit;
class QPushButton;
class QTableWidget;

class TransactionHistoryWidget : public QWidget
{
    Q_OBJECT

public:
    explicit TransactionHistoryWidget(WalletEngine* engine, QWidget* parent = nullptr);

public slots:
    void refresh();

private slots:
    void handleRefreshFinished();
    void onDetailsRequested();
    void handleDetailsFinished();
    void exportJson();
    void exportCsv();

private:
    void populateWalletFilter();
    void renderTable();

    WalletEngine* m_engine;
    QComboBox* m_walletFilter;
    QComboBox* m_directionFilter;
    QComboBox* m_statusFilter;
    QLineEdit* m_searchEdit;
    QCheckBox* m_useDateFilter;
    QDateEdit* m_fromDateEdit;
    QDateEdit* m_toDateEdit;
    QPushButton* m_refreshButton;
    QPushButton* m_exportJsonButton;
    QPushButton* m_exportCsvButton;
    QTableWidget* m_table;
    QFutureWatcher<QJsonObject>* m_refreshWatcher;
    QFutureWatcher<QJsonObject>* m_detailsWatcher;
    QJsonArray m_items;
};

#endif // TRANSACTIONHISTORYWIDGET_H
