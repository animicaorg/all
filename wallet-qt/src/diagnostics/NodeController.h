#ifndef NODECONTROLLER_H
#define NODECONTROLLER_H

#include <QObject>
#include <QJsonObject>
#include <QString>
#include <QDateTime>

class AnimicaRpcClient;

/**
 * @brief Node status and action controller for diagnostics UI.
 * 
 * Provides high-level interface for:
 * - Querying node status (chain, sync, peers, mempool)
 * - Parsing status into UI-friendly models
 * - Triggering operational actions (bootstrap, sync control)
 * - Audit logging for operator actions
 * 
 * Status is fetched via node.getStatus RPC call.
 */
class NodeController : public QObject
{
    Q_OBJECT

public:
    struct ChainStatus {
        int chainId;
        qint64 headHeight;
        QString headHash;
        qint64 headTimestamp;
        qint64 bestHeaderHeight;
        QString bestHeaderHash;
    };

    struct SyncStatus {
        QString phase;          // "SYNCING", "SYNCED", "IDLE"
        double progress;        // 0.0 to 1.0
        qint64 currentHeight;
        qint64 targetHeight;
        int inFlightHeaders;
        int queueDepth;
    };

    struct PeerStatus {
        int inbound;
        int outbound;
        int total;
        QStringList listenAddrs;
    };

    struct MempoolStatus {
        int txCount;
        int rejectedLast1h;
    };

    struct HashrateStatus {
        double hashrateSps;     // Hashes per second
        int windowBlocks;
    };

    struct NodeStatus {
        bool available;
        ChainStatus chain;
        SyncStatus sync;
        PeerStatus peers;
        MempoolStatus mempool;
        HashrateStatus hashrate;
        QDateTime fetchTime;
    };

    explicit NodeController(AnimicaRpcClient* rpcClient, QObject* parent = nullptr);

    /**
     * @brief Query node status.
     * @return Node status structure
     */
    NodeStatus queryStatus();

    /**
     * @brief Trigger bootstrap (connect to public bootstrap RPC).
     * @param operatorName Name of operator for audit log (empty = use system user)
     * @return Result message
     */
    QString triggerBootstrap(const QString& operatorName = QString());

    /**
     * @brief Force sync round.
     * @param operatorName Name of operator for audit log (empty = use system user)
     * @return Result message
     */
    QString forceSyncRound(const QString& operatorName = QString());

    /**
     * @brief Pause sync.
     * @param operatorName Name of operator for audit log (empty = use system user)
     * @return Result message
     */
    QString pauseSync(const QString& operatorName = QString());

    /**
     * @brief Resume sync.
     * @param operatorName Name of operator for audit log (empty = use system user)
     * @return Result message
     */
    QString resumeSync(const QString& operatorName = QString());

signals:
    /**
     * @brief Emitted when operator action is logged.
     * @param timestamp Action timestamp
     * @param action Action name
     * @param operator Operator name
     * @param result Action result
     */
    void actionLogged(const QDateTime& timestamp, const QString& action, 
                     const QString& operatorName, const QString& result);

private:
    NodeStatus parseNodeStatus(const QJsonObject& json);
    QString executeRpcSync(const QString& method, const QJsonValue& params = QJsonValue());
    void logAction(const QString& action, const QString& operatorName, const QString& result);
    QString getOperatorName(const QString& providedName);

    AnimicaRpcClient* m_rpcClient;
};

#endif // NODECONTROLLER_H
