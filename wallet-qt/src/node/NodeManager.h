#ifndef NODEMANAGER_H
#define NODEMANAGER_H

#include <QObject>
#include <QProcess>
#include <QString>
#include <QTimer>
#include <QDateTime>
#include <QFile>
#include "../rpc/AnimicaRpcClient.h"
#include "../platform/DataDirManager.h"

class TestNodeManager;

/**
 * @brief Manages the lifecycle of the embedded Animica node.
 * 
 * Responsibilities:
 * - Launch node process with correct configuration
 * - Monitor node health via RPC ping
 * - Track process state (running, crashed, stopped)
 * - Handle graceful shutdown and restart
 * - Provide sync progress information
 * - Tail node logs
 * - Detect port conflicts and auto-increment
 * - Prevent multiple instances via lock file
 * 
 * Lifecycle:
 * 1. User clicks "Start"
 * 2. NodeManager checks for port conflicts
 * 3. NodeManager creates lock file
 * 4. NodeManager launches Python RPC server via QProcess
 * 5. NodeManager waits for RPC readiness (ping)
 * 6. NodeManager emits nodeReady()
 * 7. UI updates to show "Running" status
 * 
 * State Machine:
 * Stopped -> Starting -> Running -> Stopping -> Stopped
 *                     -> Error -> Stopped
 */
class NodeManager : public QObject
{
    Q_OBJECT

public:
    enum class State {
        Stopped,
        Starting,
        ProcessRunning, // Embedded node process launched, RPC may still be booting
        RpcReady,      // RPC is responding but may not be fully synced
        P2PReady,      // RPC responding and at least one peer connected
        Syncing,       // P2P connected and chain sync in progress
        Synced,        // P2P connected and local chain caught up
        Healthy,       // Backwards-compatible alias for fully healthy state
        Degraded,      // RPC works but P2P/sync issues detected
        Stopping,
        Error
    };
    Q_ENUM(State)

    struct NodeInfo {
        qint64 pid;
        int rpcPort;
        int p2pPort;
        QString network;
        QString pythonPath;
        QDateTime startTime;
        QString version;
        
        QString toJson() const;
        static NodeInfo fromJson(const QString& json);
    };

    explicit NodeManager(QObject* parent = nullptr);
    explicit NodeManager(DataDirManager* dataDirManager, QObject* parent = nullptr);
    ~NodeManager() override;

    /**
     * @brief Start the node process.
     * @param network Network to connect to (mainnet, testnet, devnet)
     * @return true if start initiated, false if already running or error
     */
    bool startNode(const QString& network = "devnet");

    /**
     * @brief Stop the node process gracefully.
     */
    void stopNode();

    /**
     * @brief Restart the node process.
     * @param network Network to connect to (uses previous if empty)
     */
    void restartNode(const QString& network = QString());

    /**
     * @brief Get current node state.
     */
    State state() const { return m_state; }

    /**
     * @brief Get current node info (only valid when running).
     */
    NodeInfo nodeInfo() const { return m_nodeInfo; }

    /**
     * @brief Check if node is running (any active state).
     */
    bool isRunning() const { 
        return m_state == State::ProcessRunning ||
               m_state == State::RpcReady || 
               m_state == State::P2PReady ||
               m_state == State::Syncing ||
               m_state == State::Synced ||
               m_state == State::Healthy || 
               m_state == State::Degraded; 
    }

    /**
     * @brief Get last error message.
     */
    QString lastError() const { return m_lastError; }

    /**
     * @brief Get node log file path.
     */
    QString logFilePath() const;

    /**
     * @brief Get last N lines from node log.
     * @param lines Number of lines to read (default: 100)
     */
    QStringList readLogLines(int lines = 100);

    /**
     * @brief Open logs folder in file manager.
     */
    void openLogsFolder();

    /**
     * @brief Copy diagnostics info to clipboard.
     * @return Diagnostics string
     */
    QString collectDiagnostics();
    
    /**
     * @brief Set the data directory manager.
     * @param dataDirManager Data directory manager instance
     */
    void setDataDirManager(DataDirManager* dataDirManager);
    
    /**
     * @brief Reset chain data directory (requires user confirmation).
     * @param chainId Chain ID to reset
     * @return true if reset successful
     */
    bool resetChainData(int chainId);
    
    /**
     * @brief Get recent log lines with deduplication.
     * @param maxLines Maximum number of lines to return
     * @return Deduplicated log lines
     */
    QStringList getDeduplicatedLogs(int maxLines = 100);

    /**
     * @brief Default bootstrap seeds used by wallet-managed embedded node.
     * @param network Network name (mainnet/testnet/devnet)
     * @return ordered seed list, highest-priority first
     */
    static QStringList defaultBootstrapSeeds(const QString& network);
    
    /**
     * @brief Check if node is in degraded state.
     * @return true if degraded
     */
    bool isDegraded() const { return m_state == State::Degraded; }

signals:
    /**
     * @brief Emitted when node state changes.
     * @param state New state
     */
    void stateChanged(State state);

    /**
     * @brief Emitted when node is ready to accept RPC calls.
     */
    void nodeReady();

    /**
     * @brief Emitted when node process exits.
     * @param exitCode Process exit code
     * @param crashed true if unexpected exit
     */
    void nodeExited(int exitCode, bool crashed);

    /**
     * @brief Emitted when an error occurs.
     * @param message Error message
     */
    void error(const QString& message);

    /**
     * @brief Emitted when sync progress updates.
     * @param currentBlock Current block height
     * @param highestBlock Highest known block
     * @param syncing true if still syncing
     */
    void syncProgress(int currentBlock, int highestBlock, bool syncing);

    /**
     * @brief Emitted when new log lines are available.
     * @param lines New log lines
     */
    void logLinesAvailable(const QStringList& lines);
    
    /**
     * @brief Emitted when degraded state is detected.
     * @param reason Reason for degradation
     */
    void nodeDegraded(const QString& reason);

    /**
     * @brief Emitted with layered health/sync telemetry suitable for UI diagnostics.
     */
    void healthTelemetryUpdated(
        int peerCount,
        int localHeight,
        int networkHeight,
        const QString& syncPhase,
        const QString& lastError,
        const QString& lastBootstrapContact,
        bool rpcReady,
        bool p2pReady,
        bool syncing,
        bool synced
    );

private slots:
    void onProcessStarted();
    void onProcessFinished(int exitCode, QProcess::ExitStatus exitStatus);
    void onProcessError(QProcess::ProcessError error);
    void onProcessOutput();
    void onHealthCheckTimeout();
    void onSyncCheckTimeout();
    void onRestartBackoffTimeout();

private:
    friend class TestNodeManager;

    void setState(State state);
    void setError(const QString& message);
    
    /**
     * @brief Find available port starting from base port.
     * @param basePort Base port to try
     * @param range Number of ports to try
     * @return Available port, or -1 if none found
     */
    int findAvailablePort(int basePort, int range = 10);

    /**
     * @brief Check if port is in use.
     */
    bool isPortInUse(int port);

    /**
     * @brief Acquire lock file to prevent multiple instances.
     * @return true if lock acquired
     */
    bool acquireLock();

    /**
     * @brief Release lock file.
     */
    void releaseLock();

    /**
     * @brief Write node info to JSON file.
     */
    void writeNodeInfo();

    /**
     * @brief Start health check timer.
     */
    void startHealthCheck();

    /**
     * @brief Stop health check timer.
     */
    void stopHealthCheck();

    /**
     * @brief Start sync status polling.
     */
    void startSyncMonitoring();

    /**
     * @brief Stop sync status polling.
     */
    void stopSyncMonitoring();

    /**
     * @brief Find Python interpreter.
     * Checks for bundled Python first, then falls back to system Python.
     * @return Path to python3 executable, or empty if not found
     */
    QString findPython();
    
    /**
     * @brief Find bundled Python from the wallet installation.
     * @return Path to bundled python executable, or empty if not bundled
     */
    QString findBundledPython();
    
    /**
     * @brief Detect problematic patterns in log output.
     * @param line Log line to check
     * @return true if degradation pattern detected
     */
    bool detectDegradationPattern(const QString& line);
    
    /**
     * @brief Add line to log buffer with deduplication.
     * @param line Log line to add
     */
    void addLogLine(const QString& line);
    
    /**
     * @brief Calculate restart delay with exponential backoff.
     * @return Delay in milliseconds
     */
    int calculateRestartDelay();

    /**
     * @brief Schedule restart with backoff.
     */
    void scheduleRestart();

    /**
     * @brief Append raw process output to the persisted node log.
     * @param text Raw process output chunk
     */
    void appendProcessLogOutput(const QString& text);

    /**
     * @brief Emit a wallet-generated log line to the UI and persisted log.
     * @param line Message to emit
     */
    void emitLocalLogLine(const QString& line);

    /**
     * @brief Track node-side recovery signals seen in stdout/stderr.
     * @param line Log line to inspect
     */
    void noteRecoverySignals(const QString& line);

    /**
     * @brief Evaluate sync progress and trigger wallet-side recovery when stalled.
     * @param currentBlock Current synced block height
     * @param highestBlock Best known target height
     * @param syncing Whether sync is still active
     */
    void evaluateSyncWatchdog(int currentBlock, int highestBlock, bool syncing);

    /**
     * @brief Trigger an RPC-level sync.force recovery attempt.
     * @param reason Human-readable reason for diagnostics/logging
     */
    void triggerWalletSyncForceRecovery(const QString& reason);

    /**
     * @brief Escalate to a local chain reset + node restart if corruption persists.
     * @param reason Human-readable reason for diagnostics/logging
     */
    void maybeScheduleEmbeddedResetRecovery(const QString& reason);

    /**
     * @brief Execute the scheduled local chain reset + embedded node restart.
     */
    void performEmbeddedResetRecovery();

    /**
     * @brief Reset wallet-side recovery counters for a fresh node session.
     */
    void resetRecoveryState();

    /**
     * @brief Perform enhanced health check.
     */
    void performHealthCheck();
    bool tryAttachToExistingNode();
    bool isProcessAlive(qint64 pid) const;
    qint64 lockFilePid(const QString& lockPath) const;
    void updateOperationalStateFromSync(int peerCount, int currentBlock, int highestBlock, bool syncing, const QString& phase);
    
    /**
     * @brief Ensure data directory exists and is valid.
     * @param chainId Chain ID
     * @return true if valid
     */
    bool ensureDataDirValid(int chainId);

    State m_state;
    QString m_lastError;
    QString m_currentNetwork;
    
    QProcess* m_process;
    AnimicaRpcClient* m_rpcClient;
    DataDirManager* m_dataDirManager;
    
    NodeInfo m_nodeInfo;
    
    QTimer* m_healthCheckTimer;
    QTimer* m_syncCheckTimer;
    QTimer* m_restartTimer;
    int m_healthCheckAttempts;
    int m_restartAttempts;
    bool m_attachedToExistingNode;
    
    QFile* m_lockFile;
    
    // Log management
    QStringList m_logBuffer;  // Ring buffer for last 5000 lines
    QMap<QString, QPair<int, QDateTime>> m_logDedupeMap;  // line -> (count, last seen)
    
    // Degradation tracking
    bool m_degradationDetected;
    QString m_degradationReason;
    int m_lastPeerCount;
    int m_lastLocalHeight;
    int m_lastNetworkHeight;
    QString m_lastSyncPhase;
    QDateTime m_lastBootstrapContactAt;
    QDateTime m_rpcReadySince;

    // Wallet-side sync watchdog tracking
    QDateTime m_syncWatchdogLastProgressAt;
    QDateTime m_syncWatchdogLastForceAt;
    int m_syncWatchdogLastCurrentBlock;
    int m_syncWatchdogLastTargetBlock;
    int m_syncWatchdogForceAttempts;
    bool m_syncForceInFlight;
    bool m_embeddedResetRecoveryScheduled;
    bool m_embeddedResetRecoveryInProgress;
    int m_embeddedResetRecoveryAttempts;

    // Windowed log counters used to distinguish transient stalls from persistent local corruption.
    int m_cursorResetWindowCount;
    QDateTime m_cursorResetWindowStartedAt;
    int m_nodeWatchdogWindowCount;
    QDateTime m_nodeWatchdogWindowStartedAt;

    static constexpr int DEFAULT_RPC_PORT = 8548;  // mainnet default from problem statement
    static constexpr int DEFAULT_P2P_PORT = 30333;
    static constexpr int HEALTH_CHECK_INITIAL_INTERVAL = 250;  // 250ms initially
    static constexpr int HEALTH_CHECK_BACKOFF_INTERVAL = 2000;  // 2s after initial attempts
    static constexpr int HEALTH_CHECK_MAX_ATTEMPTS = 120;  // 30 seconds initially (250ms * 120), then keep trying with backoff
    static constexpr int SYNC_CHECK_INTERVAL = 5000;  // 5 seconds
    static constexpr int SYNC_CHECK_DEGRADED_INTERVAL = 15000;  // 15 seconds when degraded
    static constexpr int SYNC_WALLET_WATCHDOG_STALL_MS = 30000;  // 30s without block-height progress
    static constexpr int SYNC_FORCE_COOLDOWN_MS = 20000;  // avoid spamming sync.force
    static constexpr int MAX_SYNC_FORCE_ATTEMPTS = 2;
    static constexpr int RECOVERY_SIGNAL_WINDOW_MS = 30000;  // correlate repeated corruption signals
    static constexpr int CURSOR_RESET_RECOVERY_THRESHOLD = 6;
    static constexpr int NODE_WATCHDOG_RECOVERY_THRESHOLD = 3;
    static constexpr int MAX_EMBEDDED_RESET_RECOVERY_ATTEMPTS = 1;
    static constexpr int MAX_LOG_BUFFER_SIZE = 5000;
    static constexpr int LOG_DEDUPE_WINDOW_MS = 2000;  // 2 second deduplication window
    static constexpr int MAX_RESTART_DELAY_MS = 60000;  // 60 seconds max backoff
};

#endif // NODEMANAGER_H
