#ifndef ACCOUNTSWIDGET_H
#define ACCOUNTSWIDGET_H

#include "WalletAccount.h"
#include "BalanceTracker.h"
#include <QWidget>
#include <QTableWidget>
#include <QPushButton>
#include <QLineEdit>
#include <QLabel>
#include <QMenu>

class WalletEngine;

/**
 * @brief Widget displaying list of wallet accounts.
 * 
 * Features:
 * - Table view with label, address, balance columns
 * - Star icon for default account
 * - Create/Import/Export action buttons
 * - Context menu: Rename, Set Default, Remove, Copy Address
 * - Double-click to view account details
 */
class AccountsWidget : public QWidget
{
    Q_OBJECT

public:
    explicit AccountsWidget(WalletEngine* engine, QWidget* parent = nullptr);
    
    /**
     * @brief Refresh account list from engine.
     */
    void refreshAccounts();
    
    /**
     * @brief Get selected account ID.
     * @return Account UUID or empty string if no selection
     */
    QString selectedAccountId() const;

signals:
    void accountSelected(const QString& accountId);
    void createAccountRequested();
    void importAccountRequested();
    void exportAccountRequested(const QString& accountId);
    void viewAccountDetailsRequested(const QString& accountId);

private slots:
    void onCreateClicked();
    void onImportClicked();
    void onExportClicked();
    void onTableDoubleClicked(int row, int column);
    void onTableSelectionChanged();
    void onContextMenuRequested(const QPoint& pos);
    void onRenameAccount();
    void onSetDefaultAccount();
    void onRemoveAccount();
    void onCopyAddress();
    void onExportPublicInfo();
    void onExportSecretBackup();
    void handleAccountAdded(const WalletAccount& account);
    void handleAccountUpdated(const WalletAccount& account);
    void handleAccountRemoved(const QString& accountId);
    void handleBalanceUpdated(const QString& address, const Balance& balance);

private:
    void setupUi();
    void updateAccountRow(int row, const WalletAccount& account);
    void showAccountDetails(const WalletAccount& account);
    QString formatAddress(const QString& address) const;
    QString formatBalance(quint64 balance) const;
    int findAccountRow(const QString& accountId) const;
    
    WalletEngine* m_engine;
    QTableWidget* m_accountTable;
    QPushButton* m_createButton;
    QPushButton* m_importButton;
    QPushButton* m_exportButton;
    QLabel* m_statusLabel;
    QMenu* m_contextMenu;
};

#endif // ACCOUNTSWIDGET_H
