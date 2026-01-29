#ifndef RECONCILIATIONJOB_H
#define RECONCILIATIONJOB_H

#include <QObject>
#include <QString>
#include <QList>
#include <QMutex>
#include <QJsonObject>

class AnimicaRpcClient;
class WalletDatabase;
class WalletEngine;
struct WalletAccount;
struct LedgerEntry;

/**
 * @brief Reconciliation job to re-derive balances from chain state and repair inconsistencies.
 * 
 * Purpose:
 * - Self-healing mechanism for wallet state corruption
 * - Compares local ledger balances with on-chain state
 * - Creates adjustment entries to fix discrepancies
 * - Maintains audit trail in reconciliation_runs table
 * 
 * Process:
 * 1. Create database backup (optional)
 * 2. Query chain balances for all accounts
 * 3. Query local ledger balances
 * 4. Compare and detect discrepancies
 * 5. Create adjustment ledger entries
 * 6. Record audit trail
 * 
 * Thread Safety:
 * - Runs reconciliation in background thread (QtConcurrent)
 * - Uses mutex for status fields
 * - Emits signals for progress and completion
 * 
 * Example Usage:
 * @code
 *   ReconciliationJob* job = new ReconciliationJob(rpc, db, engine, this);
 *   connect(job, &ReconciliationJob::completed, this, &MyClass::onReconciliationCompleted);
 *   connect(job, &ReconciliationJob::discrepancyFound, this, &MyClass::onDiscrepancy);
 *   job->start();
 * @endcode
 */
class ReconciliationJob : public QObject
{
    Q_OBJECT
    
public:
    /**
     * @brief Construct reconciliation job.
     * @param rpcClient RPC client for querying chain state
     * @param database Wallet database for local state
     * @param walletEngine Wallet engine for account list
     * @param parent QObject parent
     */
    explicit ReconciliationJob(
        AnimicaRpcClient* rpcClient,
        WalletDatabase* database,
        WalletEngine* walletEngine,
        QObject* parent = nullptr
    );
    
    ~ReconciliationJob();
    
    // ==================== Control ====================
    
    /**
     * @brief Start reconciliation in background thread.
     * @note Emits started() signal if successful, failed() if already running
     */
    void start();
    
    /**
     * @brief Cancel running reconciliation.
     * @note Sets cancellation flag; job may complete before checking flag
     */
    void cancel();
    
    // ==================== Status ====================
    
    /**
     * @brief Check if reconciliation is running.
     * @return true if running
     */
    bool isRunning() const;
    
    /**
     * @brief Get current run ID.
     * @return Run UUID (empty if not running)
     */
    QString currentRunId() const;
    
    // ==================== Configuration ====================
    
    /**
     * @brief Enable/disable automatic backup before reconciliation.
     * @param enable true to create backup (default)
     */
    void setCreateBackup(bool enable);
    
    /**
     * @brief Check if backup is enabled.
     * @return true if backups are enabled
     */
    bool createBackupEnabled() const;
    
signals:
    /**
     * @brief Emitted when reconciliation starts.
     * @param runId Unique run ID for this reconciliation
     */
    void started(const QString& runId);
    
    /**
     * @brief Emitted during reconciliation progress.
     * @param percentage Progress percentage (0-100)
     * @param step Current step description
     */
    void progress(int percentage, const QString& step);
    
    /**
     * @brief Emitted when reconciliation completes successfully.
     * @param runId Run ID
     * @param summary Summary object with before/after balances and changes
     */
    void completed(const QString& runId, const QJsonObject& summary);
    
    /**
     * @brief Emitted when reconciliation fails.
     * @param runId Run ID
     * @param error Error message
     */
    void failed(const QString& runId, const QString& error);
    
    /**
     * @brief Emitted when a balance discrepancy is found.
     * @param accountId Account UUID
     * @param expected Expected balance from chain
     * @param actual Actual balance in local ledger
     */
    void discrepancyFound(const QString& accountId, qint64 expected, qint64 actual);
    
private:
    /**
     * @brief Account balance snapshot.
     */
    struct AccountBalance {
        QString accountId;
        QString address;
        qint64 confirmedChain;    // Balance from chain state
        qint64 confirmedLocal;    // Balance from local ledger (AVAILABLE)
        qint64 pendingLocal;      // Pending balance (PENDING_IN - PENDING_OUT)
        qint64 discrepancy;       // confirmedChain - confirmedLocal
        
        AccountBalance()
            : confirmedChain(0)
            , confirmedLocal(0)
            , pendingLocal(0)
            , discrepancy(0)
        {
        }
    };
    
    // ==================== Internal Methods ====================
    
    /**
     * @brief Main reconciliation logic (runs in background thread).
     */
    void runReconciliation();
    
    /**
     * @brief Create database backup.
     * @return true if backup created successfully
     */
    bool createBackup();
    
    /**
     * @brief Query balances from chain for all accounts.
     * @return List of account balances from chain
     */
    QList<AccountBalance> queryChainBalances();
    
    /**
     * @brief Query balances from local ledger for all accounts.
     * @return List of account balances from ledger
     */
    QList<AccountBalance> queryLocalBalances();
    
    /**
     * @brief Compare chain and local balances.
     * @param chain Chain balances
     * @param local Local balances
     * @return List of accounts with discrepancies
     */
    QList<AccountBalance> compareBalances(
        const QList<AccountBalance>& chain,
        const QList<AccountBalance>& local
    );
    
    /**
     * @brief Repair discrepancies by creating adjustment entries.
     * @param discrepancies List of accounts with discrepancies
     * @return true if all repairs successful
     */
    bool repairDiscrepancies(const QList<AccountBalance>& discrepancies);
    
    /**
     * @brief Build summary JSON object.
     * @param before Balances before reconciliation
     * @param after Balances after reconciliation
     * @param repaired Number of accounts repaired
     * @return Summary object
     */
    QJsonObject buildSummary(
        const QList<AccountBalance>& before,
        const QList<AccountBalance>& after,
        int repaired
    );
    
    // ==================== Member Variables ====================
    
    AnimicaRpcClient* m_rpcClient;
    WalletDatabase* m_database;
    WalletEngine* m_walletEngine;
    
    QString m_runId;
    bool m_running;
    bool m_cancelled;
    bool m_createBackup;
    
    mutable QMutex m_mutex;
};

#endif // RECONCILIATIONJOB_H
