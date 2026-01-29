#ifndef WALLETWIDGET_H
#define WALLETWIDGET_H

#include "BalanceTracker.h"
#include <QWidget>
#include <QTabWidget>
#include <QToolBar>
#include <QStatusBar>
#include <QLabel>
#include <QAction>

class WalletEngine;
class AccountsWidget;
class AddressBookWidget;

/**
 * @brief Main wallet UI coordinator.
 * 
 * Integrates:
 * - Accounts list
 * - Address book
 * - Balance display
 * 
 * Features:
 * - Toolbar with Lock/Unlock/Create Account actions
 * - Status bar showing lock state and last update
 * - Tabbed interface for different views
 */
class WalletWidget : public QWidget
{
    Q_OBJECT

public:
    explicit WalletWidget(WalletEngine* engine, QWidget* parent = nullptr);
    
    /**
     * @brief Refresh all wallet data.
     */
    void refresh();
    
    /**
     * @brief Get wallet engine.
     */
    WalletEngine* engine() const { return m_engine; }

signals:
    void lockRequested();
    void unlockRequested();

private slots:
    void onLockAction();
    void onUnlockAction();
    void onCreateAccountAction();
    void onRefreshAction();
    void handleWalletLocked();
    void handleWalletUnlocked();
    void handleBalanceUpdated(const QString& address, const Balance& balance);
    void handleSyncStatusChanged(bool syncing);
    void handleCreateAccountRequested();
    void updateStatus();

private:
    void setupUi();
    void updateToolbarState();
    QString formatTotalBalance() const;
    
    WalletEngine* m_engine;
    
    // UI components
    QToolBar* m_toolbar;
    QTabWidget* m_tabWidget;
    QLabel* m_statusLabel;
    QLabel* m_balanceLabel;
    QLabel* m_syncLabel;
    
    // Actions
    QAction* m_lockAction;
    QAction* m_unlockAction;
    QAction* m_createAccountAction;
    QAction* m_refreshAction;
    
    // Child widgets
    AccountsWidget* m_accountsWidget;
    AddressBookWidget* m_addressBookWidget;
};

#endif // WALLETWIDGET_H
