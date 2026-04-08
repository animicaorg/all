#ifndef BALANCETRACKER_H
#define BALANCETRACKER_H

#include <QObject>
#include <QString>
#include <QTimer>
#include <QMap>

// Forward declaration
class AnimicaRpcClient;

/**
 * @brief Balance data for an account.
 */
struct Balance {
    QString address;
    quint64 confirmed;      // In smallest unit (1 ANM = 10^9)
    quint64 pending;
    QString asset;          // "ANM" or token address
    bool syncing;
    int lastSyncHeight;
    
    Balance()
        : confirmed(0)
        , pending(0)
        , asset("ANM")
        , syncing(false)
        , lastSyncHeight(0)
    {
    }
};

/**
 * @brief Tracks account balances via RPC polling.
 * 
 * Polls node RPC for balance updates at regular intervals.
 * Emits signals when balances change.
 */
class BalanceTracker : public QObject
{
    Q_OBJECT

public:
    explicit BalanceTracker(AnimicaRpcClient* rpcClient, QObject* parent = nullptr);
    
    /**
     * @brief Start tracking addresses.
     * @param addresses List of bech32m addresses to track
     */
    void startTracking(const QStringList& addresses);
    
    /**
     * @brief Stop tracking all addresses.
     */
    void stopTracking();
    
    /**
     * @brief Get current balances.
     * @return Map of address -> balance
     */
    QMap<QString, Balance> getBalances() const;
    
    /**
     * @brief Get balance for specific address.
     * @param address Bech32m address
     * @return Balance or default balance if not found
     */
    Balance getBalance(const QString& address) const;
    
    /**
     * @brief Force immediate balance refresh.
     */
    void refresh();
    
    /**
     * @brief Set polling interval.
     * @param intervalMs Interval in milliseconds (default: 5000)
     */
    void setPollingInterval(int intervalMs);
    
    /**
     * @brief Check if tracking is active.
     * @return true if tracking
     */
    bool isTracking() const { return m_tracking; }

signals:
    void balanceUpdated(const QString& address, const Balance& balance);
    void syncStatusChanged(bool syncing);
    void error(const QString& message);

private slots:
    void pollBalances();
    void handleBalanceReply();
    void handleSyncStatusReply();

private:
    void fetchBalance(const QString& address);
    void fetchSyncStatus();

    AnimicaRpcClient* m_rpcClient;
    QTimer m_pollTimer;
    QStringList m_addresses;
    QMap<QString, Balance> m_balances;
    bool m_tracking;
    bool m_syncing;
};

#endif // BALANCETRACKER_H
