#ifndef NODEMANAGER_H
#define NODEMANAGER_H

#include <QObject>
#include <QProcess>
#include <QString>
#include <QTimer>
#include <QDateTime>
#include <QFile>
#include "../rpc/AnimicaRpcClient.h"

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
        Running,
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
     * @brief Check if node is running.
     */
    bool isRunning() const { return m_state == State::Running; }

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

private slots:
    void onProcessStarted();
    void onProcessFinished(int exitCode, QProcess::ExitStatus exitStatus);
    void onProcessError(QProcess::ProcessError error);
    void onProcessOutput();
    void onHealthCheckTimeout();
    void onSyncCheckTimeout();

private:
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

    State m_state;
    QString m_lastError;
    QString m_currentNetwork;
    
    QProcess* m_process;
    AnimicaRpcClient* m_rpcClient;
    
    NodeInfo m_nodeInfo;
    
    QTimer* m_healthCheckTimer;
    QTimer* m_syncCheckTimer;
    int m_healthCheckAttempts;
    
    QFile* m_lockFile;
    
    static constexpr int DEFAULT_RPC_PORT = 8545;
    static constexpr int DEFAULT_P2P_PORT = 30333;
    static constexpr int HEALTH_CHECK_TIMEOUT = 1000;  // 1 second
    static constexpr int HEALTH_CHECK_MAX_ATTEMPTS = 30;  // 30 seconds total
    static constexpr int SYNC_CHECK_INTERVAL = 5000;  // 5 seconds
};

#endif // NODEMANAGER_H
