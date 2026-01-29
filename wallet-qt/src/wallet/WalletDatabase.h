#ifndef WALLETDATABASE_H
#define WALLETDATABASE_H

#include <QObject>
#include <QString>
#include <QList>
#include <QMutex>
#include <QSqlDatabase>
#include <QByteArray>

/**
 * @brief Transaction record in wallet journal.
 * 
 * Represents a transaction tracked by the wallet with complete lifecycle metadata.
 * Supports inbound, outbound, and self-transfer transactions.
 */
struct WalletTx {
    QString txid;                   // Transaction hash (hex)
    QString direction;              // "in", "out", "self"
    QString fromAccountId;          // Sending account UUID (empty for "in")
    QString toAddress;              // Recipient address (bech32m)
    qint64 amount;                  // Transfer amount in wei (atomic units)
    qint64 fee;                     // Transaction fee in wei
    QString state;                  // CREATED, SIGNED, BROADCAST, MEMPOOL, CONFIRMED, FINAL, DROPPED, REORGED, FAILED
    qint64 firstSeenAt;             // Unix timestamp milliseconds
    qint64 lastUpdateAt;            // Unix timestamp milliseconds
    QString blockHash;              // Block hash (empty if not mined)
    qint64 blockHeight;             // Block height (-1 if not mined)
    int confirmations;              // Confirmation count
    QByteArray rawTx;               // Raw transaction bytes (optional, can be encrypted)
    QString failureReason;          // Error message if FAILED state
    
    WalletTx()
        : amount(0)
        , fee(0)
        , firstSeenAt(0)
        , lastUpdateAt(0)
        , blockHeight(-1)
        , confirmations(0)
    {
    }
    
    /**
     * @brief Check if transaction is valid.
     * @return true if txid is not empty
     */
    bool isValid() const { return !txid.isEmpty(); }
};

/**
 * @brief Ledger entry for double-entry accounting.
 * 
 * Each transaction creates one or more ledger entries to track balance changes
 * across different states (available, pending in/out, fee reserved).
 */
struct LedgerEntry {
    qint64 entryId;                 // Auto-increment ID
    QString txid;                   // Transaction hash
    QString accountId;              // Account UUID
    QString asset;                  // Asset identifier ("ANM" for native token)
    QString type;                   // AVAILABLE, PENDING_IN, PENDING_OUT, FEE_RESERVED
    qint64 delta;                   // Signed balance change (positive = increase, negative = decrease)
    qint64 stateVersion;            // Monotonic version counter for ordering
    qint64 createdAt;               // Unix timestamp milliseconds
    
    LedgerEntry()
        : entryId(0)
        , delta(0)
        , stateVersion(0)
        , createdAt(0)
    {
    }
};

/**
 * @brief SQLite-backed wallet database for transaction journal and double-entry accounting.
 * 
 * Provides persistent storage for:
 * - Transaction journal (wallet_tx table)
 * - Double-entry ledger (wallet_ledger_entry table)
 * - Idempotency tracking (idempotency_keys table)
 * - Reconciliation audit trail (reconciliation_runs table)
 * 
 * Features:
 * - Thread-safe operations with QMutex
 * - Atomic transactions for consistency
 * - State version counter for ordering
 * - Balance invariant enforcement
 * - Comprehensive error handling
 * 
 * State Transitions:
 * CREATED → SIGNED → BROADCAST → MEMPOOL → CONFIRMED → FINAL
 *     ↓        ↓          ↓           ↓         ↓
 *  FAILED   FAILED    FAILED      DROPPED   REORGED
 */
class WalletDatabase : public QObject
{
    Q_OBJECT

public:
    /**
     * @brief Construct wallet database.
     * @param dbPath Full path to SQLite database file
     * @param parent QObject parent
     */
    explicit WalletDatabase(const QString& dbPath, QObject* parent = nullptr);
    
    /**
     * @brief Destructor - closes database connection.
     */
    ~WalletDatabase();
    
    /**
     * @brief Initialize database (create tables if needed).
     * @return true if initialized successfully
     */
    bool initialize();
    
    // ==================== Transaction Journal ====================
    
    /**
     * @brief Add new transaction to journal.
     * @param tx Transaction to add
     * @return true if added successfully
     */
    bool addTransaction(const WalletTx& tx);
    
    /**
     * @brief Update existing transaction.
     * @param txid Transaction ID to update
     * @param tx Updated transaction data
     * @return true if updated successfully
     */
    bool updateTransaction(const QString& txid, const WalletTx& tx);
    
    /**
     * @brief Get transaction by ID.
     * @param txid Transaction ID
     * @return Transaction or invalid transaction if not found
     */
    WalletTx getTransaction(const QString& txid);
    
    /**
     * @brief List transactions for account.
     * @param accountId Account UUID (empty = all transactions)
     * @return List of transactions ordered by lastUpdateAt descending
     */
    QList<WalletTx> listTransactions(const QString& accountId = QString());
    
    /**
     * @brief Delete transaction (use with caution).
     * @param txid Transaction ID to delete
     * @return true if deleted successfully
     */
    bool deleteTransaction(const QString& txid);
    
    // ==================== Ledger Entries ====================
    
    /**
     * @brief Add ledger entry (with balance invariant check).
     * @param entry Ledger entry to add
     * @return true if added successfully, false if would violate balance invariant
     */
    bool addLedgerEntry(const LedgerEntry& entry);
    
    /**
     * @brief Get all ledger entries for transaction.
     * @param txid Transaction ID
     * @return List of ledger entries ordered by stateVersion
     */
    QList<LedgerEntry> getLedgerEntries(const QString& txid);
    
    /**
     * @brief Get ledger entries for account.
     * @param accountId Account UUID
     * @return List of ledger entries ordered by stateVersion
     */
    QList<LedgerEntry> getAccountLedger(const QString& accountId);
    
    /**
     * @brief List all ledger entries (for monitoring/history).
     * @return List of all ledger entries
     */
    QList<LedgerEntry> listLedgerEntries();
    
    /**
     * @brief Delete a ledger entry by ID.
     * @param ledgerId Entry ID to delete
     * @return true if deleted successfully
     */
    bool deleteLedgerEntry(qint64 ledgerId);
    
    /**
     * @brief Get available balance for account.
     * @param accountId Account UUID
     * @param asset Asset identifier (default: "ANM")
     * @return Available balance in atomic units
     */
    qint64 getBalance(const QString& accountId, const QString& asset = "ANM");
    
    /**
     * @brief Get pending balance (incoming minus outgoing).
     * @param accountId Account UUID
     * @param asset Asset identifier (default: "ANM")
     * @return Pending balance in atomic units (can be negative)
     */
    qint64 getPendingBalance(const QString& accountId, const QString& asset = "ANM");
    
    // ==================== State Version ====================
    
    /**
     * @brief Get next state version number.
     * @return Monotonically increasing version counter
     * @note State version is managed in memory. If a database transaction is rolled back,
     *       the state version counter will not be rolled back, creating gaps in the sequence.
     *       This is acceptable as the counter only needs to be monotonic, not contiguous.
     */
    qint64 nextStateVersion();
    
    // ==================== Idempotency ====================
    
    /**
     * @brief Check if event has been processed.
     * @param key Idempotency key (format: "txid:event_type:event_source:event_seq")
     * @return true if already processed, false if not processed or on database error
     * @note Returns false on database error - callers should check error signal separately
     */
    bool checkIdempotency(const QString& key);
    
    /**
     * @brief Mark event as processed.
     * @param key Idempotency key
     * @return true if marked successfully
     */
    bool markProcessed(const QString& key);
    
    // ==================== Reconciliation ====================
    
    /**
     * @brief Start reconciliation run.
     * @return Unique run ID (UUID)
     */
    QString startReconciliation();
    
    /**
     * @brief Record before/after snapshots.
     * @param runId Reconciliation run ID
     * @param beforeJson JSON snapshot before reconciliation
     * @param afterJson JSON snapshot after reconciliation
     * @return true if recorded successfully
     */
    bool recordReconciliationSnapshot(const QString& runId, const QString& beforeJson, const QString& afterJson);
    
    /**
     * @brief Complete reconciliation run successfully.
     * @param runId Reconciliation run ID
     * @param changesJson JSON array of changes applied
     * @return true if completed successfully
     */
    bool completeReconciliation(const QString& runId, const QString& changesJson);
    
    /**
     * @brief Mark reconciliation run as failed.
     * @param runId Reconciliation run ID
     * @param errorMsg Error message
     * @return true if marked failed successfully
     */
    bool failReconciliation(const QString& runId, const QString& errorMsg);
    
    // ==================== Transaction Support ====================
    
    /**
     * @brief Begin database transaction.
     * @return true if transaction started successfully
     * @note The mutex is held only during this call, not for the entire transaction.
     *       Callers must ensure external synchronization if transaction isolation is required
     *       across multiple threads. For single-threaded usage, transaction isolation is guaranteed.
     */
    bool beginTransaction();
    
    /**
     * @brief Commit database transaction.
     * @return true if committed successfully
     */
    bool commit();
    
    /**
     * @brief Rollback database transaction.
     * @return true if rolled back successfully
     */
    bool rollback();

signals:
    /**
     * @brief Emitted when transaction is added to journal.
     * @param tx Added transaction
     */
    void transactionAdded(const WalletTx& tx);
    
    /**
     * @brief Emitted when transaction is updated.
     * @param tx Updated transaction
     */
    void transactionUpdated(const WalletTx& tx);
    
    /**
     * @brief Emitted when ledger is updated for account.
     * @param accountId Account UUID
     */
    void ledgerUpdated(const QString& accountId);
    
    /**
     * @brief Emitted on database errors.
     * @param message Error message
     */
    void error(const QString& message);

private:
    /**
     * @brief Create database tables if they don't exist.
     * @return true if tables created or already exist
     */
    bool createTables();
    
    /**
     * @brief Get current Unix timestamp in milliseconds.
     * @return Timestamp
     */
    qint64 getCurrentTimestamp();
    
    /**
     * @brief Validate state transition.
     * @param oldState Current state
     * @param newState Proposed new state
     * @return true if transition is valid
     */
    bool isValidStateTransition(const QString& oldState, const QString& newState);
    
    /**
     * @brief Check if adding entry would violate balance invariant (internal, assumes mutex held).
     * @param entry Entry to check
     * @return true if balance would remain non-negative
     */
    bool checkBalanceInvariant(const LedgerEntry& entry);
    
    /**
     * @brief Get transaction by ID (internal, assumes mutex held).
     * @param txid Transaction ID
     * @return Transaction or invalid transaction if not found
     */
    WalletTx getTransactionUnlocked(const QString& txid);
    
    /**
     * @brief Get available balance (internal, assumes mutex held).
     * @param accountId Account UUID
     * @param asset Asset identifier
     * @return Available balance in atomic units
     */
    qint64 getBalanceUnlocked(const QString& accountId, const QString& asset);
    
    QString m_dbPath;               // Database file path
    QSqlDatabase m_db;              // Qt SQL database connection
    qint64 m_stateVersion;          // Current state version counter
    QMutex m_mutex;                 // Thread safety mutex
    QString m_connectionName;       // Unique connection name for this instance
};

#endif // WALLETDATABASE_H
