#ifndef WALLETWIDGET_H
#define WALLETWIDGET_H

#include "BalanceTracker.h"
#include <QFrame>
#include <QWidget>
#include <QTabWidget>
#include <QToolBar>
#include <QStatusBar>
#include <QLabel>
#include <QAction>

class AnimicaRpcClient;
class WalletDatabase;
class TransactionMonitor;
class WalletEngine;
class AccountsWidget;
class AddressBookWidget;
class SendWidget;
class ReceiveWidget;
class TransactionHistoryWidget;
class ContractInteractionWidget;
class SettingsWidget;

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
    explicit WalletWidget(
        WalletEngine* engine,
        AnimicaRpcClient* rpcClient,
        WalletDatabase* database,
        TransactionMonitor* monitor,
        QWidget* parent = nullptr
    );
    
    /**
     * @brief Refresh all wallet data.
     */
    void refresh();
    
    /**
     * @brief Get wallet engine.
     */
    WalletEngine* engine() const { return m_engine; }

    void setRpcEndpoint(const QString& endpoint);

signals:
    void settingsRequested();

private slots:
    void onCreateAccountAction();
    void onRefreshAction();
    void handleWalletLocked();
    void handleWalletUnlocked();
    void handleBalanceUpdated(const QString& address, const Balance& balance);
    void handleSyncStatusChanged(bool syncing);
    void handleCreateAccountRequested();
    void updateStatus();
    void handleRpcConnected();
    void handleRpcDisconnected();
    void handleRpcError(const QString& message);
    void retryRpcProbe();

private:
    void setupUi();
    void updateToolbarState();
    QString formatTotalBalance() const;
    void updateRpcStatusLabel(const QString& status, const QString& color);
    void probeRpcStatus();
    void setConnectionBanner(const QString& title, const QString& details);
    void clearConnectionBanner();
    
    WalletEngine* m_engine;
    AnimicaRpcClient* m_rpcClient;
    WalletDatabase* m_database;
    TransactionMonitor* m_monitor;
    
    // UI components
    QToolBar* m_toolbar;
    QTabWidget* m_tabWidget;
    QFrame* m_connectionBanner;
    QLabel* m_connectionBannerTitle;
    QLabel* m_connectionBannerDetails;
    QLabel* m_statusLabel;
    QLabel* m_balanceLabel;
    QLabel* m_syncLabel;
    QLabel* m_rpcStatusLabel;
    QLabel* m_rpcEndpointLabel;
    QAction* m_retryConnectionAction;
    
    // Actions
    QAction* m_createAccountAction;
    QAction* m_refreshAction;
    
    // Child widgets
    AccountsWidget* m_accountsWidget;
    AddressBookWidget* m_addressBookWidget;
    SendWidget* m_sendWidget;
    ReceiveWidget* m_receiveWidget;
    TransactionHistoryWidget* m_historyWidget;
    ContractInteractionWidget* m_contractWidget;
    SettingsWidget* m_settingsWidget;
    QString m_lastRpcError;
};

#endif // WALLETWIDGET_H
