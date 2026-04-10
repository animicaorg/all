#ifndef TRANSACTIONMONITOR_H
#define TRANSACTIONMONITOR_H

#include <QObject>
#include <QTimer>
#include <QMutex>
#include <QSet>
#include <QMap>
#include <QString>
#include <QStringList>
#include <QJsonObject>
#include <QDateTime>

class AnimicaRpcClient;
class WalletDatabase;
struct WalletTx;

class TransactionMonitor : public QObject {
    Q_OBJECT
    
public:
    explicit TransactionMonitor(
        AnimicaRpcClient* rpcClient,
        WalletDatabase* database,
        QObject* parent = nullptr
    );
    ~TransactionMonitor();
    
    // Control
    void start();
    void stop();
    bool isRunning() const;
    
    // Track transactions
    void trackTransaction(const QString& txHash, const QString& direction);
    void stopTracking(const QString& txHash);
    QStringList trackedTransactions() const;
    
    // WebSocket support
    void enableWebSocket(bool enable);
    bool isWebSocketEnabled() const;
    
    // Polling configuration
    void setPollInterval(int milliseconds);
    void setFastPollInterval(int milliseconds);
    int pollInterval() const;
    
    // Confirmation threshold
    void setConfirmationThreshold(int confirmations);
    int confirmationThreshold() const;
    
signals:
    // Transaction lifecycle events
    void transactionSeen(const QString& txHash);
    void transactionMined(const QString& txHash, qint64 blockHeight, const QString& blockHash);
    void transactionConfirmed(const QString& txHash, int confirmations);
    void transactionFinalized(const QString& txHash);
    void transactionDropped(const QString& txHash, const QString& reason);
    void transactionReorged(const QString& txHash, qint64 oldHeight, qint64 newHeight);
    
    // Error events
    void error(const QString& message);
    void rpcConnectionLost();
    void rpcConnectionRestored();
    
private slots:
    void onPollTimer();
    void onFastPollTimer();
    void onNewHead(const QJsonObject& head);
    void onPendingTx(const QJsonObject& tx);
    
private:
    // Core monitoring
    void checkTransaction(const QString& txHash);
    void updateConfirmations();
    void detectReorgs();
    
    // WebSocket
    void subscribeToEvents();
    void unsubscribeFromEvents();
    
    // Polling strategy
    void switchToFastPolling(const QString& txHash);
    void switchToNormalPolling();
    
    // Reorg detection
    bool isBlockStillCanonical(const QString& blockHash, qint64 height);
    void handleReorg(const QString& txHash, const WalletTx& tx);
    
    // State management
    void updateTransactionState(const QString& txHash, const QString& newState);
    void creditPending(const QString& txHash, const QString& accountId, qint64 amount);
    void creditConfirmed(const QString& txHash, const QString& accountId, qint64 amount);
    void ensureOutgoingReservation(const QString& txHash, const QString& accountId, qint64 amount, qint64 feeReserve);
    void clearOutgoingReservation(const QString& txHash, const QString& accountId);
    void revertCredit(const QString& txHash, const QString& accountId);
    
    // Error handling
    void handleRpcError(const QString& errorMsg);
    
    AnimicaRpcClient* m_rpcClient;
    WalletDatabase* m_database;
    
    QTimer* m_pollTimer;
    QTimer* m_fastPollTimer;
    
    bool m_running;
    bool m_wsEnabled;
    int m_pollInterval;
    int m_fastPollInterval;
    int m_confirmationThreshold;
    
    QSet<QString> m_trackedTxs;
    QSet<QString> m_fastPollTxs;
    
    // Reorg tracking
    struct BlockInfo {
        qint64 height;
        QString hash;
    };
    QMap<qint64, BlockInfo> m_knownBlocks;
    
    QString m_lastHeadHash;
    qint64 m_lastHeadHeight;
    
    // Error tracking
    int m_consecutiveErrors;
    qint64 m_lastErrorTime;
    bool m_connectionLost;
    qint64 m_lastActivityTime;
    
    mutable QMutex m_mutex;
};

#endif // TRANSACTIONMONITOR_H
