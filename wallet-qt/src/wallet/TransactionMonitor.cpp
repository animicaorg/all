#include "TransactionMonitor.h"
#include "WalletDatabase.h"
#include "../rpc/AnimicaRpcClient.h"
#include <QDebug>
#include <QMutexLocker>
#include <QJsonArray>

namespace {
qint64 parseStatusIntField(const QJsonObject& status, const char* key, qint64 fallback = -1)
{
    const QJsonValue value = status.value(QLatin1String(key));
    if (value.isUndefined() || value.isNull()) {
        return fallback;
    }
    if (value.isDouble()) {
        return static_cast<qint64>(value.toDouble());
    }
    if (value.isString()) {
        QString text = value.toString().trimmed();
        int base = 10;
        if (text.startsWith("0x")) {
            text = text.mid(2);
            base = 16;
        }
        bool ok = false;
        const qint64 parsed = text.toLongLong(&ok, base);
        if (ok) {
            return parsed;
        }
        return fallback;
    }
    return value.toVariant().toLongLong();
}

QString normalizedStatus(const QJsonObject& status)
{
    auto normalizeToken = [](const QJsonValue& value) -> QString {
        const QString token = value.toString().trimmed().toLower();
        if (token.isEmpty()) {
            return QString();
        }

        if (token == "pending" || token == "pending_mempool" || token == "mempool_accepted" || token == "broadcast") {
            return "pending";
        }
        if (token == "confirmed" || token == "included" || token == "included_block"
            || token == "in_block_pending_confirm" || token == "instant_confirmed" || token == "mined") {
            return "confirmed";
        }
        if (token == "finalized" || token == "final" || token == "success" || token == "succeeded"
            || token == "applied") {
            return "finalized";
        }
        if (token == "failed" || token == "rejected" || token == "dropped" || token == "evicted") {
            return "rejected";
        }
        if (token == "reorged_out" || token == "reorged") {
            return "reorged_out";
        }
        if (token == "not_found") {
            return "not_found";
        }

        return token;
    };

    QString normalized = normalizeToken(status.value("status"));
    if (normalized.isEmpty()) {
        normalized = normalizeToken(status.value("state"));
    }

    if (normalized.isEmpty() || normalized == "pending") {
        QString blockHash = status.value("included_in_block_hash").toString();
        if (blockHash.isEmpty()) {
            blockHash = status.value("includedInBlockHash").toString();
        }
        if (blockHash.isEmpty()) {
            blockHash = status.value("blockHash").toString();
        }
        if (!blockHash.isEmpty()) {
            normalized = status.value("finalized").toBool() ? "finalized" : "confirmed";
        }
    }

    if (normalized.isEmpty()) {
        return "not_found";
    }
    return normalized;
}

qint64 statusBlockHeight(const QJsonObject& status)
{
    qint64 height = parseStatusIntField(status, "included_height");
    if (height < 0) {
        height = parseStatusIntField(status, "includedHeight");
    }
    if (height < 0) {
        height = parseStatusIntField(status, "blockNumber");
    }
    return height;
}

QString statusBlockHash(const QJsonObject& status)
{
    QString blockHash = status.value("included_in_block_hash").toString();
    if (blockHash.isEmpty()) {
        blockHash = status.value("includedInBlockHash").toString();
    }
    if (blockHash.isEmpty()) {
        blockHash = status.value("blockHash").toString();
    }
    return blockHash;
}

int statusConfirmations(const QJsonObject& status)
{
    return static_cast<int>(parseStatusIntField(status, "confirmations", 0));
}

QString statusFailureReason(const QJsonObject& status)
{
    QString reason = status.value("reason").toString();
    if (reason.isEmpty()) {
        reason = status.value("error").toString();
    }
    if (reason.isEmpty()) {
        reason = status.value("details").toString();
    }
    return reason;
}
}

TransactionMonitor::TransactionMonitor(
    AnimicaRpcClient* rpcClient,
    WalletDatabase* database,
    QObject* parent
)
    : QObject(parent)
    , m_rpcClient(rpcClient)
    , m_database(database)
    , m_pollTimer(new QTimer(this))
    , m_fastPollTimer(new QTimer(this))
    , m_running(false)
    , m_wsEnabled(false)
    , m_pollInterval(10000)
    , m_fastPollInterval(2000)
    , m_confirmationThreshold(10)
    , m_lastHeadHeight(-1)
    , m_consecutiveErrors(0)
    , m_lastErrorTime(0)
    , m_connectionLost(false)
    , m_lastActivityTime(0)
{
    connect(m_pollTimer, &QTimer::timeout, this, &TransactionMonitor::onPollTimer);
    connect(m_fastPollTimer, &QTimer::timeout, this, &TransactionMonitor::onFastPollTimer);
    
    qDebug() << "TransactionMonitor created";
}

TransactionMonitor::~TransactionMonitor() {
    stop();
    qDebug() << "TransactionMonitor destroyed";
}

void TransactionMonitor::start() {
    QMutexLocker locker(&m_mutex);
    
    if (m_running) {
        qWarning() << "TransactionMonitor already running";
        return;
    }
    
    qDebug() << "Starting TransactionMonitor";
    m_running = true;
    m_consecutiveErrors = 0;
    m_connectionLost = false;
    m_lastActivityTime = QDateTime::currentMSecsSinceEpoch();
    
    // Load tracked transactions from database
    QList<WalletTx> txs = m_database->listTransactions();
    for (const WalletTx& tx : txs) {
        if (tx.state != "FINAL" && tx.state != "DROPPED") {
            m_trackedTxs.insert(tx.txid);
            
            // Add to fast poll if pending or recently mined
            if (tx.state == "MEMPOOL" || 
                tx.state == "CONFIRMED") {
                m_fastPollTxs.insert(tx.txid);
            }
        }
    }
    
    qDebug() << "Tracking" << m_trackedTxs.size() << "transactions," 
             << m_fastPollTxs.size() << "need fast polling";
    
    // Start appropriate polling
    if (!m_fastPollTxs.isEmpty()) {
        m_fastPollTimer->start(m_fastPollInterval);
    } else {
        m_pollTimer->start(m_pollInterval);
    }
    
    if (m_wsEnabled) {
        subscribeToEvents();
    }
}

void TransactionMonitor::stop() {
    QMutexLocker locker(&m_mutex);
    
    if (!m_running) {
        return;
    }
    
    qDebug() << "Stopping TransactionMonitor";
    m_running = false;
    
    m_pollTimer->stop();
    m_fastPollTimer->stop();
    
    if (m_wsEnabled) {
        unsubscribeFromEvents();
    }
}

bool TransactionMonitor::isRunning() const {
    QMutexLocker locker(&m_mutex);
    return m_running;
}

void TransactionMonitor::trackTransaction(const QString& txHash, const QString& direction) {
    QMutexLocker locker(&m_mutex);
    
    qDebug() << "Tracking transaction:" << txHash << "direction:" << direction;
    
    if (m_trackedTxs.contains(txHash)) {
        qDebug() << "Transaction already tracked";
        return;
    }
    
    m_trackedTxs.insert(txHash);
    
    // New transactions need fast polling
    switchToFastPolling(txHash);
    
    m_lastActivityTime = QDateTime::currentMSecsSinceEpoch();
    
    // Immediate check
    locker.unlock();
    checkTransaction(txHash);
}

void TransactionMonitor::stopTracking(const QString& txHash) {
    QMutexLocker locker(&m_mutex);
    
    qDebug() << "Stop tracking transaction:" << txHash;
    
    m_trackedTxs.remove(txHash);
    m_fastPollTxs.remove(txHash);
}

QStringList TransactionMonitor::trackedTransactions() const {
    QMutexLocker locker(&m_mutex);
    return m_trackedTxs.values();
}

void TransactionMonitor::enableWebSocket(bool enable) {
    QMutexLocker locker(&m_mutex);
    
    if (m_wsEnabled == enable) {
        return;
    }
    
    qDebug() << "WebSocket support" << (enable ? "enabled" : "disabled");
    m_wsEnabled = enable;
    
    if (m_running) {
        if (enable) {
            subscribeToEvents();
        } else {
            unsubscribeFromEvents();
        }
    }
}

bool TransactionMonitor::isWebSocketEnabled() const {
    QMutexLocker locker(&m_mutex);
    return m_wsEnabled;
}

void TransactionMonitor::setPollInterval(int milliseconds) {
    QMutexLocker locker(&m_mutex);
    m_pollInterval = milliseconds;
    if (m_running && m_pollTimer->isActive()) {
        m_pollTimer->setInterval(milliseconds);
    }
}

void TransactionMonitor::setFastPollInterval(int milliseconds) {
    QMutexLocker locker(&m_mutex);
    m_fastPollInterval = milliseconds;
    if (m_running && m_fastPollTimer->isActive()) {
        m_fastPollTimer->setInterval(milliseconds);
    }
}

int TransactionMonitor::pollInterval() const {
    QMutexLocker locker(&m_mutex);
    return m_pollInterval;
}

void TransactionMonitor::setConfirmationThreshold(int confirmations) {
    QMutexLocker locker(&m_mutex);
    qDebug() << "Setting confirmation threshold to" << confirmations;
    m_confirmationThreshold = confirmations;
}

int TransactionMonitor::confirmationThreshold() const {
    QMutexLocker locker(&m_mutex);
    return m_confirmationThreshold;
}

void TransactionMonitor::onPollTimer() {
    QMutexLocker locker(&m_mutex);
    
    if (!m_running) {
        return;
    }
    
    qDebug() << "Poll timer fired - checking" << m_trackedTxs.size() << "transactions";
    
    QSet<QString> txsToCheck = m_trackedTxs;
    locker.unlock();
    
    for (const QString& txHash : txsToCheck) {
        checkTransaction(txHash);
    }
    
    updateConfirmations();
    detectReorgs();
}

void TransactionMonitor::onFastPollTimer() {
    QMutexLocker locker(&m_mutex);
    
    if (!m_running) {
        return;
    }
    
    qDebug() << "Fast poll timer fired - checking" << m_fastPollTxs.size() << "transactions";
    
    QSet<QString> txsToCheck = m_fastPollTxs;
    locker.unlock();
    
    for (const QString& txHash : txsToCheck) {
        checkTransaction(txHash);
    }
    
    updateConfirmations();
    detectReorgs();
    
    // Check if we should switch back to normal polling
    locker.relock();
    qint64 now = QDateTime::currentMSecsSinceEpoch();
    if (m_fastPollTxs.isEmpty() || (now - m_lastActivityTime > 60000)) {
        switchToNormalPolling();
    }
}

void TransactionMonitor::onNewHead(const QJsonObject& head) {
    qDebug() << "New head received:" << head["hash"].toString() 
             << "height:" << head["height"].toVariant().toLongLong();
    
    m_lastHeadHash = head["hash"].toString();
    m_lastHeadHeight = head["height"].toVariant().toLongLong();
    
    updateConfirmations();
    detectReorgs();
}

void TransactionMonitor::onPendingTx(const QJsonObject& tx) {
    QString txHash = tx["hash"].toString();
    
    QMutexLocker locker(&m_mutex);
    if (m_trackedTxs.contains(txHash)) {
        qDebug() << "Tracked transaction seen in mempool:" << txHash;
        locker.unlock();
        
        emit transactionSeen(txHash);
        updateTransactionState(txHash, "MEMPOOL");
    }
}

void TransactionMonitor::checkTransaction(const QString& txHash) {
    try {
        QJsonObject txInfo = m_rpcClient->getTransactionStatusByHash(txHash);
        
        if (txInfo.isEmpty()) {
            qWarning() << "Transaction not found:" << txHash;
            return;
        }
        
        // Reset error tracking on successful RPC call
        {
            QMutexLocker locker(&m_mutex);
            if (m_connectionLost) {
                qDebug() << "RPC connection restored";
                m_connectionLost = false;
                m_consecutiveErrors = 0;
                emit rpcConnectionRestored();
            }
        }
        
        WalletTx tx = m_database->getTransaction(txHash);
        if (tx.txid.isEmpty()) {
            qWarning() << "Transaction not in database:" << txHash;
            return;
        }
        
        const QString status = normalizedStatus(txInfo);
        qDebug() << "Transaction" << txHash << "status:" << status;
        
        if (status == "pending") {
            if (tx.state != "MEMPOOL") {
                updateTransactionState(txHash, "MEMPOOL");
                emit transactionSeen(txHash);
                
                // Credit pending if it's an incoming tx
                if (tx.direction == "in") {
                    creditPending(txHash, tx.fromAccountId, tx.amount);
                }
                
                QMutexLocker locker(&m_mutex);
                switchToFastPolling(txHash);
            }

            if (tx.direction == "out") {
                ensureOutgoingReservation(txHash, tx.fromAccountId, tx.amount, tx.fee);
            }
        } else if (status == "confirmed" || status == "finalized") {
            const qint64 blockHeight = statusBlockHeight(txInfo);
            const QString blockHash = statusBlockHash(txInfo);
            
            if (tx.state == "MEMPOOL" || tx.state == "BROADCAST" || tx.state == "REORGED") {
                // Newly included on chain (1+ confirmations)
                WalletTx updated = tx;
                updated.state = "CONFIRMED";
                updated.blockHash = blockHash;
                updated.blockHeight = blockHeight;
                int confirmations = statusConfirmations(txInfo);
                if (confirmations <= 0) {
                    confirmations = 1;
                }
                updated.confirmations = confirmations;
                updated.lastUpdateAt = QDateTime::currentMSecsSinceEpoch();
                m_database->updateTransaction(txHash, updated);
                
                emit transactionMined(txHash, blockHeight, blockHash);

                if (tx.direction == "out") {
                    clearOutgoingReservation(txHash, tx.fromAccountId);
                }
                
                // Track this block for reorg detection
                QMutexLocker locker(&m_mutex);
                if (blockHeight >= 0 && !blockHash.isEmpty()) {
                    BlockInfo info;
                    info.height = blockHeight;
                    info.hash = blockHash;
                    m_knownBlocks[blockHeight] = info;
                }
                switchToFastPolling(txHash);
                m_lastActivityTime = QDateTime::currentMSecsSinceEpoch();
                
            } else if (tx.blockHash != blockHash || tx.blockHeight != blockHeight) {
                // Block changed - possible reorg
                qWarning() << "Transaction moved blocks (reorg?):" << txHash 
                          << "old:" << tx.blockHash << "@" << tx.blockHeight
                          << "new:" << blockHash << "@" << blockHeight;
                handleReorg(txHash, tx);
            }
        } else if (status == "reorged_out") {
            if (tx.state != "REORGED") {
                handleReorg(txHash, tx);
            }
        } else if (status == "failed" || status == "rejected") {
            if (tx.state != "DROPPED") {
                WalletTx updated = tx;
                updated.state = "DROPPED";
                updated.failureReason = statusFailureReason(txInfo);
                if (updated.failureReason.isEmpty()) {
                    updated.failureReason = "Transaction " + status;
                }
                updated.lastUpdateAt = QDateTime::currentMSecsSinceEpoch();
                m_database->updateTransaction(txHash, updated);
                
                emit transactionDropped(txHash, updated.failureReason);

                if (tx.direction == "in") {
                    revertCredit(txHash, tx.fromAccountId);
                } else if (tx.direction == "out") {
                    clearOutgoingReservation(txHash, tx.fromAccountId);
                }
                
                QMutexLocker locker(&m_mutex);
                m_fastPollTxs.remove(txHash);
            }
        }
        
    } catch (const std::exception& e) {
        handleRpcError(QString("Error checking transaction %1: %2").arg(txHash, e.what()));
    } catch (...) {
        handleRpcError(QString("Unknown error checking transaction %1").arg(txHash));
    }
}

void TransactionMonitor::updateConfirmations() {
    try {
        QJsonObject head = m_rpcClient->getHeadJson();
        if (head.isEmpty()) {
            qWarning() << "Failed to get chain head";
            return;
        }
        
        qint64 currentHeight = head["height"].toVariant().toLongLong();
        QString currentHash = head["hash"].toString();
        
        QMutexLocker locker(&m_mutex);
        m_lastHeadHash = currentHash;
        m_lastHeadHeight = currentHeight;
        locker.unlock();
        
        QList<WalletTx> txs = m_database->listTransactions();
        for (const WalletTx& tx : txs) {
            if (tx.state != "CONFIRMED") {
                continue;
            }
            
            if (tx.blockHeight < 0) {
                continue;
            }
            
            int confirmations = static_cast<int>(currentHeight - tx.blockHeight + 1);

            const bool shouldFinalize = confirmations >= m_confirmationThreshold;
            if (!shouldFinalize && confirmations == tx.confirmations) {
                continue;
            }

            WalletTx updated = tx;
            updated.confirmations = confirmations;
            updated.lastUpdateAt = QDateTime::currentMSecsSinceEpoch();

            if (shouldFinalize) {
                // Transition to FINAL
                updated.state = "FINAL";
                m_database->updateTransaction(tx.txid, updated);

                qDebug() << "Transaction finalized:" << tx.txid
                         << "confirmations:" << confirmations;
                emit transactionFinalized(tx.txid);

                // Move balance from pending to available
                if (tx.direction == "in") {
                    creditConfirmed(tx.txid, tx.fromAccountId, tx.amount);
                } else if (tx.direction == "out") {
                    // Ensure any stale reservation entries are removed once finalized.
                    clearOutgoingReservation(tx.txid, tx.fromAccountId);
                }

                // Stop fast polling
                locker.relock();
                m_fastPollTxs.remove(tx.txid);
                locker.unlock();

            } else {
                // Just update confirmation count
                m_database->updateTransaction(tx.txid, updated);

                if (confirmations % 5 == 0) {
                    qDebug() << "Transaction" << tx.txid
                             << "confirmations:" << confirmations;
                    emit transactionConfirmed(tx.txid, confirmations);
                }
            }
        }
        
        // Reset error tracking
        locker.relock();
        if (m_connectionLost) {
            qDebug() << "RPC connection restored";
            m_connectionLost = false;
            m_consecutiveErrors = 0;
            emit rpcConnectionRestored();
        }
        
    } catch (const std::exception& e) {
        handleRpcError(QString("Error updating confirmations: %1").arg(e.what()));
    } catch (...) {
        handleRpcError("Unknown error updating confirmations");
    }
}

void TransactionMonitor::detectReorgs() {
    QMutexLocker locker(&m_mutex);
    QList<qint64> heights = m_knownBlocks.keys();
    locker.unlock();
    
    for (qint64 height : heights) {
        locker.relock();
        if (!m_knownBlocks.contains(height)) {
            continue;
        }
        BlockInfo block = m_knownBlocks[height];
        locker.unlock();
        
        if (!isBlockStillCanonical(block.hash, block.height)) {
            qWarning() << "Reorg detected at height" << height 
                      << "block" << block.hash << "is no longer canonical";
            
            // Find affected transactions
            QList<WalletTx> txs = m_database->listTransactions();
            for (const WalletTx& tx : txs) {
                if (tx.blockHeight == block.height && tx.blockHash == block.hash) {
                    qWarning() << "Transaction affected by reorg:" << tx.txid;
                    handleReorg(tx.txid, tx);
                }
            }
            
            // Remove this block from known blocks
            locker.relock();
            m_knownBlocks.remove(height);
            locker.unlock();
        }
    }
}

bool TransactionMonitor::isBlockStillCanonical(const QString& blockHash, qint64 height) {
    try {
        QJsonObject block = m_rpcClient->getBlockByNumberJson(height, false);
        if (block.isEmpty()) {
            qWarning() << "Block not found at height" << height;
            return false;
        }
        
        QString canonicalHash = block["hash"].toString();
        return canonicalHash == blockHash;
        
    } catch (const std::exception& e) {
        qWarning() << "Error checking canonical block:" << e.what();
        return true; // Assume canonical on error to avoid false positives
    } catch (...) {
        qWarning() << "Unknown error checking canonical block";
        return true;
    }
}

void TransactionMonitor::handleReorg(const QString& txHash, const WalletTx& tx) {
    qWarning() << "Handling reorg for transaction:" << txHash;
    
    // Mark as reorged
    WalletTx updated = tx;
    updated.state = "REORGED";
    updated.lastUpdateAt = QDateTime::currentMSecsSinceEpoch();
    m_database->updateTransaction(txHash, updated);
    
    emit transactionReorged(txHash, tx.blockHeight, -1);
    
    // Revert balance changes
    if (tx.direction == "in") {
        revertCredit(txHash, tx.fromAccountId);
    } else if (tx.direction == "out") {
        clearOutgoingReservation(txHash, tx.fromAccountId);
    }
    
    // Check current status
    try {
        QJsonObject txInfo = m_rpcClient->getTransactionStatusByHash(txHash);
        
        if (!txInfo.isEmpty()) {
            const QString status = normalizedStatus(txInfo);
            
            if (status == "pending") {
                // Back to mempool
                qDebug() << "Transaction back in mempool:" << txHash;
                updated.state = "MEMPOOL";
                updated.blockHash = QString();
                updated.blockHeight = -1;
                updated.confirmations = 0;
                m_database->updateTransaction(txHash, updated);

                if (tx.direction == "in") {
                    creditPending(txHash, tx.fromAccountId, tx.amount);
                } else if (tx.direction == "out") {
                    ensureOutgoingReservation(txHash, tx.fromAccountId, tx.amount, tx.fee);
                }
                
                QMutexLocker locker(&m_mutex);
                switchToFastPolling(txHash);
                
            } else if (status == "confirmed" || status == "finalized") {
                // Mined in different block
                const qint64 newHeight = statusBlockHeight(txInfo);
                const QString newHash = statusBlockHash(txInfo);
                
                qDebug() << "Transaction mined in new block:" << txHash 
                        << "height:" << newHeight << "hash:" << newHash;
                
                updated.state = "CONFIRMED";
                updated.blockHash = newHash;
                updated.blockHeight = newHeight;
                int confirmations = statusConfirmations(txInfo);
                if (confirmations <= 0) {
                    confirmations = 1;
                }
                updated.confirmations = confirmations;
                m_database->updateTransaction(txHash, updated);
                
                emit transactionMined(txHash, newHeight, newHash);
                
                if (tx.direction == "in") {
                    creditPending(txHash, tx.fromAccountId, tx.amount);
                } else if (tx.direction == "out") {
                    clearOutgoingReservation(txHash, tx.fromAccountId);
                }
                
                QMutexLocker locker(&m_mutex);
                if (newHeight >= 0 && !newHash.isEmpty()) {
                    BlockInfo info;
                    info.height = newHeight;
                    info.hash = newHash;
                    m_knownBlocks[newHeight] = info;
                }
                switchToFastPolling(txHash);
            } else if (status == "not_found") {
                // Dropped completely
                qWarning() << "Transaction not found after reorg:" << txHash;
                updated.state = "DROPPED";
                updated.failureReason = "Reorg: transaction not in new chain";
                m_database->updateTransaction(txHash, updated);
                
                emit transactionDropped(txHash, updated.failureReason);
                
                QMutexLocker locker(&m_mutex);
                m_fastPollTxs.remove(txHash);
            }
        } else {
            // Dropped completely
            qWarning() << "Transaction not found after reorg:" << txHash;
            updated.state = "DROPPED";
            updated.failureReason = "Reorg: transaction not in new chain";
            m_database->updateTransaction(txHash, updated);
            
            emit transactionDropped(txHash, updated.failureReason);
            
            QMutexLocker locker(&m_mutex);
            m_fastPollTxs.remove(txHash);
        }
        
    } catch (const std::exception& e) {
        qCritical() << "Error checking transaction after reorg:" << e.what();
        emit error(QString("Reorg handling error: %1").arg(e.what()));
    }
}

void TransactionMonitor::updateTransactionState(const QString& txHash, const QString& newState) {
    WalletTx tx = m_database->getTransaction(txHash);
    if (tx.txid.isEmpty()) {
        qWarning() << "Transaction not found in database:" << txHash;
        return;
    }
    
    if (tx.state == newState) {
        return;
    }
    
    qDebug() << "Updating transaction state:" << txHash 
             << "from" << tx.state << "to" << newState;
    
    tx.state = newState;
    tx.lastUpdateAt = QDateTime::currentMSecsSinceEpoch();
    m_database->updateTransaction(txHash, tx);
}

void TransactionMonitor::creditPending(const QString& txHash, const QString& accountId, qint64 amount) {
    if (accountId.isEmpty() || amount <= 0) {
        return;
    }
    
    qDebug() << "Credit pending:" << accountId << "amount:" << amount << "tx:" << txHash;
    
    // Check if already credited
    QList<LedgerEntry> entries = m_database->listLedgerEntries();
    for (const LedgerEntry& entry : entries) {
        if (entry.txid == txHash && entry.type == "PENDING_IN") {
            qDebug() << "Already credited as pending";
            return;
        }
    }
    
    LedgerEntry entry;
    entry.entryId = 0; // Auto-increment
    entry.accountId = accountId;
    entry.type = "PENDING_IN";
    entry.delta = amount; // Positive for credit
    entry.txid = txHash;
    entry.asset = "ANM";
    entry.stateVersion = m_database->nextStateVersion();
    entry.createdAt = QDateTime::currentMSecsSinceEpoch();
    
    if (!m_database->addLedgerEntry(entry)) {
        qCritical() << "Failed to add pending ledger entry";
        emit error("Failed to credit pending balance");
    }
}

void TransactionMonitor::creditConfirmed(const QString& txHash, const QString& accountId, qint64 amount) {
    if (accountId.isEmpty() || amount <= 0) {
        return;
    }
    
    qDebug() << "Credit confirmed:" << accountId << "amount:" << amount << "tx:" << txHash;
    
    // Remove PENDING_IN entry
    QList<LedgerEntry> entries = m_database->listLedgerEntries();
    for (const LedgerEntry& entry : entries) {
        if (entry.txid == txHash && entry.type == "PENDING_IN") {
            m_database->deleteLedgerEntry(entry.entryId);
            break;
        }
    }
    
    // Add AVAILABLE entry
    LedgerEntry entry;
    entry.entryId = 0; // Auto-increment
    entry.accountId = accountId;
    entry.type = "AVAILABLE";
    entry.delta = amount; // Positive for credit
    entry.txid = txHash;
    entry.asset = "ANM";
    entry.stateVersion = m_database->nextStateVersion();
    entry.createdAt = QDateTime::currentMSecsSinceEpoch();
    
    if (!m_database->addLedgerEntry(entry)) {
        qCritical() << "Failed to add confirmed ledger entry";
        emit error("Failed to credit confirmed balance");
    }
}

void TransactionMonitor::ensureOutgoingReservation(
    const QString& txHash,
    const QString& accountId,
    qint64 amount,
    qint64 feeReserve
) {
    if (accountId.isEmpty()) {
        return;
    }

    bool hasPendingOut = false;
    bool hasFeeReserve = false;
    const QList<LedgerEntry> entries = m_database->getLedgerEntries(txHash);
    for (const LedgerEntry& entry : entries) {
        if (entry.accountId != accountId) {
            continue;
        }
        if (entry.type == "PENDING_OUT") {
            hasPendingOut = true;
        } else if (entry.type == "FEE_RESERVED") {
            hasFeeReserve = true;
        }
    }

    const qint64 now = QDateTime::currentMSecsSinceEpoch();
    if (!hasPendingOut && amount > 0) {
        LedgerEntry pendingOut;
        pendingOut.entryId = 0;
        pendingOut.txid = txHash;
        pendingOut.accountId = accountId;
        pendingOut.asset = "ANM";
        pendingOut.type = "PENDING_OUT";
        pendingOut.delta = -amount;
        pendingOut.stateVersion = m_database->nextStateVersion();
        pendingOut.createdAt = now;
        m_database->addLedgerEntry(pendingOut);
    }

    if (!hasFeeReserve && feeReserve > 0) {
        LedgerEntry feeEntry;
        feeEntry.entryId = 0;
        feeEntry.txid = txHash;
        feeEntry.accountId = accountId;
        feeEntry.asset = "ANM";
        feeEntry.type = "FEE_RESERVED";
        feeEntry.delta = -feeReserve;
        feeEntry.stateVersion = m_database->nextStateVersion();
        feeEntry.createdAt = now;
        m_database->addLedgerEntry(feeEntry);
    }
}

void TransactionMonitor::clearOutgoingReservation(const QString& txHash, const QString& accountId) {
    const QList<LedgerEntry> entries = m_database->getLedgerEntries(txHash);
    for (const LedgerEntry& entry : entries) {
        if (entry.type == "PENDING_OUT" || entry.type == "FEE_RESERVED") {
            if (!accountId.isEmpty() && entry.accountId != accountId) {
                continue;
            }
            m_database->deleteLedgerEntry(entry.entryId);
        }
    }
}

void TransactionMonitor::revertCredit(const QString& txHash, const QString& accountId) {
    qDebug() << "Reverting credit for tx:" << txHash << "account:" << accountId;
    
    // Remove all ledger entries for this transaction
    QList<LedgerEntry> entries = m_database->listLedgerEntries();
    for (const LedgerEntry& entry : entries) {
        if (entry.txid == txHash) {
            if (!accountId.isEmpty() && entry.accountId != accountId) {
                continue;
            }
            qDebug() << "Removing ledger entry:" << entry.entryId 
                     << "type:" << entry.type << "delta:" << entry.delta;
            m_database->deleteLedgerEntry(entry.entryId);
        }
    }
    
    // Note: No need to add a separate reversal entry since we're removing the original entries
    // The deletion of entries already reverses the balance effect
}

void TransactionMonitor::subscribeToEvents() {
    qDebug() << "Subscribing to WebSocket events";
    
    // TODO: Implement WebSocket subscription
    // This would connect to the RPC client's WebSocket signals
    // For now, we rely on polling
    
    qWarning() << "WebSocket subscription not yet implemented - using polling only";
}

void TransactionMonitor::unsubscribeFromEvents() {
    qDebug() << "Unsubscribing from WebSocket events";
    
    // TODO: Implement WebSocket unsubscription
}

void TransactionMonitor::switchToFastPolling(const QString& txHash) {
    // Assumes mutex is already locked
    
    if (!m_fastPollTxs.contains(txHash)) {
        qDebug() << "Switching to fast polling for:" << txHash;
        m_fastPollTxs.insert(txHash);
        m_lastActivityTime = QDateTime::currentMSecsSinceEpoch();
    }
    
    // Switch timers if needed
    if (!m_fastPollTimer->isActive() && m_running) {
        m_pollTimer->stop();
        m_fastPollTimer->start(m_fastPollInterval);
        qDebug() << "Started fast poll timer";
    }
}

void TransactionMonitor::switchToNormalPolling() {
    // Assumes mutex is already locked
    
    if (m_fastPollTimer->isActive()) {
        qDebug() << "Switching to normal polling";
        m_fastPollTimer->stop();
        
        if (m_running && !m_pollTimer->isActive()) {
            m_pollTimer->start(m_pollInterval);
        }
    }
}

void TransactionMonitor::handleRpcError(const QString& errorMsg) {
    QMutexLocker locker(&m_mutex);
    
    qWarning() << "RPC error:" << errorMsg;
    
    m_consecutiveErrors++;
    m_lastErrorTime = QDateTime::currentMSecsSinceEpoch();
    
    emit error(errorMsg);
    
    // Connection considered lost after 3 consecutive errors
    if (m_consecutiveErrors >= 3 && !m_connectionLost) {
        qCritical() << "RPC connection lost after" << m_consecutiveErrors << "consecutive errors";
        m_connectionLost = true;
        emit rpcConnectionLost();
    }
    
    // Exponential backoff on errors
    if (m_consecutiveErrors > 0) {
        int backoffMs = qMin(m_pollInterval * (1 << (m_consecutiveErrors - 1)), 60000);
        qDebug() << "Backing off for" << backoffMs << "ms";
        
        if (m_fastPollTimer->isActive()) {
            m_fastPollTimer->setInterval(backoffMs);
        } else if (m_pollTimer->isActive()) {
            m_pollTimer->setInterval(backoffMs);
        }
    }
}
