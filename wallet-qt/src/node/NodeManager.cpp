#include "NodeManager.h"
#include "../platform/AppPaths.h"
#include "../platform/DataDirManager.h"
#include <QCoreApplication>
#include <QStandardPaths>
#include <QJsonDocument>
#include <QJsonObject>
#include "../rpc/RpcReply.h"
#include <QDesktopServices>
#include <QFileInfo>
#include <QTextStream>
#include <QTcpSocket>
#include <QRandomGenerator>
#include <QDebug>

NodeManager::NodeManager(QObject* parent)
    : QObject(parent)
    , m_state(State::Stopped)
    , m_process(new QProcess(this))
    , m_rpcClient(new AnimicaRpcClient(this))
    , m_dataDirManager(nullptr)
    , m_healthCheckTimer(new QTimer(this))
    , m_syncCheckTimer(new QTimer(this))
    , m_restartTimer(new QTimer(this))
    , m_healthCheckAttempts(0)
    , m_restartAttempts(0)
    , m_lockFile(nullptr)
    , m_degradationDetected(false)
{
    // Configure process
    m_process->setProcessChannelMode(QProcess::MergedChannels);
    
    // Connect process signals
    connect(m_process, &QProcess::started, this, &NodeManager::onProcessStarted);
    connect(m_process, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished),
            this, &NodeManager::onProcessFinished);
    connect(m_process, &QProcess::errorOccurred, this, &NodeManager::onProcessError);
    connect(m_process, &QProcess::readyReadStandardOutput, this, &NodeManager::onProcessOutput);
    
    // Configure timers
    m_healthCheckTimer->setSingleShot(false);
    m_healthCheckTimer->setInterval(HEALTH_CHECK_INITIAL_INTERVAL);
    connect(m_healthCheckTimer, &QTimer::timeout, this, &NodeManager::onHealthCheckTimeout);
    
    m_syncCheckTimer->setSingleShot(false);
    m_syncCheckTimer->setInterval(SYNC_CHECK_INTERVAL);
    connect(m_syncCheckTimer, &QTimer::timeout, this, &NodeManager::onSyncCheckTimeout);
    
    m_restartTimer->setSingleShot(true);
    connect(m_restartTimer, &QTimer::timeout, this, &NodeManager::onRestartBackoffTimeout);
    
    // Ensure directories exist
    AppPaths::ensureDirectoriesExist();
}

NodeManager::NodeManager(DataDirManager* dataDirManager, QObject* parent)
    : NodeManager(parent)
{
    m_dataDirManager = dataDirManager;
}

NodeManager::~NodeManager()
{
    if (isRunning() || m_state == State::Starting) {
        stopNode();
    }
    releaseLock();
}

bool NodeManager::startNode(const QString& network)
{
    if (m_state != State::Stopped && m_state != State::Error) {
        qWarning() << "Cannot start node: already starting or running";
        return false;
    }
    
    m_currentNetwork = network;
    m_degradationDetected = false;
    m_degradationReason.clear();
    setState(State::Starting);
    
    // Determine chain ID from network
    int chainId = 1337;  // devnet
    if (network == "mainnet") chainId = 1;
    else if (network == "testnet") chainId = 2;
    
    // Check network compatibility if using DataDirManager
    if (m_dataDirManager) {
        QString errorMsg;
        if (!m_dataDirManager->checkNetworkCompatibility(network, errorMsg)) {
            setError(errorMsg);
            setState(State::Error);
            return false;
        }
        
        // Ensure directories exist
        m_dataDirManager->ensureDirectoriesExist();
        
        // Set network marker
        m_dataDirManager->setStoredNetworkId(network);
    }
    
    // Ensure data directory is valid
    if (!ensureDataDirValid(chainId)) {
        setError("Failed to initialize data directory");
        setState(State::Error);
        return false;
    }
    
    // Check for existing lock
    if (!acquireLock()) {
        setError("Node is already running (lock file exists)");
        setState(State::Error);
        return false;
    }
    
    // Find available ports (use mainnet port 8548 as default)
    int rpcPort = findAvailablePort(DEFAULT_RPC_PORT);
    if (rpcPort < 0) {
        setError("No available RPC ports in range");
        setState(State::Error);
        releaseLock();
        return false;
    }
    
    int p2pPort = findAvailablePort(DEFAULT_P2P_PORT);
    if (p2pPort < 0) {
        setError("No available P2P ports in range");
        setState(State::Error);
        releaseLock();
        return false;
    }
    
    // Find Python interpreter
    QString python = findPython();
    if (python.isEmpty()) {
        setError("Python 3.11+ not found. Please install Python.");
        setState(State::Error);
        releaseLock();
        return false;
    }
    
    // Build node info
    m_nodeInfo.pid = 0;  // Will be set after process starts
    m_nodeInfo.rpcPort = rpcPort;
    m_nodeInfo.p2pPort = p2pPort;
    m_nodeInfo.network = network;
    m_nodeInfo.pythonPath = python;
    m_nodeInfo.startTime = QDateTime::currentDateTime();
    
    // Set up RPC client
    m_rpcClient->setEndpoint(QString("http://127.0.0.1:%1/rpc").arg(rpcPort));
    
    // Determine data directory
    QString dataDir;
    if (m_dataDirManager) {
        dataDir = m_dataDirManager->getChainDataDir(chainId);
    } else {
        dataDir = AppPaths::nodeChainDir(chainId);
    }
    
    // Set environment variables
    QProcessEnvironment env = QProcessEnvironment::systemEnvironment();
    env.insert("ANIMICA_RPC_HOST", "127.0.0.1");
    env.insert("ANIMICA_RPC_PORT", QString::number(rpcPort));
    env.insert("ANIMICA_P2P_PORT", QString::number(p2pPort));
    env.insert("ANIMICA_DATA_DIR", dataDir);
    env.insert("ANIMICA_NETWORK", network);
    env.insert("ANIMICA_CHAIN_ID", QString::number(chainId));
    env.insert("ANIMICA_LOG_LEVEL", "INFO");
    env.insert("PYTHONUNBUFFERED", "1");  // Disable Python output buffering
    
    m_process->setProcessEnvironment(env);
    
    // Build command: python -m rpc
    QStringList args;
    args << "-m" << "rpc";
    
    qDebug() << "Starting node:" << python << args.join(" ");
    qDebug() << "RPC port:" << rpcPort << "P2P port:" << p2pPort;
    qDebug() << "Network:" << network << "Chain ID:" << chainId;
    qDebug() << "Data dir:" << dataDir;
    
    // Reset restart attempts on successful start
    m_restartAttempts = 0;
    
    // Start process
    m_process->start(python, args);
    
    return true;
}

void NodeManager::stopNode()
{
    if (m_state == State::Stopped) {
        return;
    }
    
    setState(State::Stopping);
    stopHealthCheck();
    stopSyncMonitoring();
    
    if (m_process->state() != QProcess::NotRunning) {
        qDebug() << "Stopping node process...";
        m_process->terminate();  // Send SIGTERM
        
        // Wait up to 5 seconds for graceful shutdown
        if (!m_process->waitForFinished(5000)) {
            qWarning() << "Node did not stop gracefully, killing...";
            m_process->kill();  // Send SIGKILL
            m_process->waitForFinished(1000);
        }
    }
    
    releaseLock();
    setState(State::Stopped);
}

void NodeManager::restartNode(const QString& network)
{
    QString net = network.isEmpty() ? m_currentNetwork : network;
    stopNode();
    
    // Brief delay before restart
    QTimer::singleShot(1000, this, [this, net]() {
        startNode(net);
    });
}

QString NodeManager::logFilePath() const
{
    return AppPaths::nodeLogFile(m_currentNetwork);
}

QStringList NodeManager::readLogLines(int lines)
{
    QFile logFile(logFilePath());
    if (!logFile.open(QIODevice::ReadOnly | QIODevice::Text)) {
        return QStringList() << "Log file not found: " + logFilePath();
    }
    
    // Read all lines
    QStringList allLines;
    QTextStream in(&logFile);
    while (!in.atEnd()) {
        allLines << in.readLine();
    }
    logFile.close();
    
    // Return last N lines
    int start = qMax(0, allLines.size() - lines);
    return allLines.mid(start);
}

void NodeManager::openLogsFolder()
{
    QString logsPath = AppPaths::logsDir();
    QDesktopServices::openUrl(QUrl::fromLocalFile(logsPath));
}

QString NodeManager::collectDiagnostics()
{
    QString diag;
    QTextStream out(&diag);
    
    out << "=== Animica Wallet Node Diagnostics ===\n\n";
    out << "Timestamp: " << QDateTime::currentDateTime().toString(Qt::ISODate) << "\n";
    out << "State: ";
    switch (m_state) {
        case State::Stopped: out << "Stopped"; break;
        case State::Starting: out << "Starting"; break;
        case State::RpcReady: out << "RpcReady"; break;
        case State::Healthy: out << "Healthy"; break;
        case State::Degraded: out << "Degraded"; break;
        case State::Stopping: out << "Stopping"; break;
        case State::Error: out << "Error"; break;
    }
    out << "\n";
    
    if (!m_lastError.isEmpty()) {
        out << "Last Error: " << m_lastError << "\n";
    }
    
    out << "\n=== Node Info ===\n";
    if (m_nodeInfo.pid > 0) {
        out << "PID: " << m_nodeInfo.pid << "\n";
        out << "RPC Port: " << m_nodeInfo.rpcPort << "\n";
        out << "P2P Port: " << m_nodeInfo.p2pPort << "\n";
        out << "Network: " << m_nodeInfo.network << "\n";
        out << "Python: " << m_nodeInfo.pythonPath << "\n";
        out << "Start Time: " << m_nodeInfo.startTime.toString(Qt::ISODate) << "\n";
        out << "Uptime: " << m_nodeInfo.startTime.secsTo(QDateTime::currentDateTime()) << " seconds\n";
    } else {
        out << "(Node not running)\n";
    }
    
    out << "\n=== Paths ===\n";
    out << "Base Dir: " << AppPaths::baseDir() << "\n";
    out << "Node Dir: " << AppPaths::nodeDir() << "\n";
    out << "Logs Dir: " << AppPaths::logsDir() << "\n";
    out << "Run Dir: " << AppPaths::runDir() << "\n";
    out << "Log File: " << logFilePath() << "\n";
    
    out << "\n=== Recent Log Lines (last 20) ===\n";
    QStringList logLines = readLogLines(20);
    for (const QString& line : logLines) {
        out << line << "\n";
    }
    
    return diag;
}

// ==================== Private Slots ====================

void NodeManager::onProcessStarted()
{
    qDebug() << "Node process started, PID:" << m_process->processId();
    m_nodeInfo.pid = m_process->processId();
    
    // Write node info to file
    writeNodeInfo();
    
    // Start health check
    m_healthCheckAttempts = 0;
    startHealthCheck();
}

void NodeManager::onProcessFinished(int exitCode, QProcess::ExitStatus exitStatus)
{
    qDebug() << "Node process finished, exit code:" << exitCode << "status:" << exitStatus;
    
    bool crashed = (exitStatus == QProcess::CrashExit) || 
                   (exitCode != 0 && m_state != State::Stopping);
    
    emit nodeExited(exitCode, crashed);
    
    if (crashed) {
        setError(QString("Node crashed with exit code %1").arg(exitCode));
        setState(State::Error);
    } else {
        setState(State::Stopped);
    }
    
    stopHealthCheck();
    stopSyncMonitoring();
    releaseLock();
}

void NodeManager::onProcessError(QProcess::ProcessError error)
{
    QString errorMsg;
    switch (error) {
        case QProcess::FailedToStart:
            errorMsg = "Failed to start node process. Is Python installed?";
            break;
        case QProcess::Crashed:
            errorMsg = "Node process crashed";
            break;
        case QProcess::Timedout:
            errorMsg = "Node process timed out";
            break;
        case QProcess::WriteError:
            errorMsg = "Write error to node process";
            break;
        case QProcess::ReadError:
            errorMsg = "Read error from node process";
            break;
        case QProcess::UnknownError:
            errorMsg = "Unknown error with node process";
            break;
    }
    
    qWarning() << "Process error:" << errorMsg;
    setError(errorMsg);
    
    if (m_state == State::Starting) {
        setState(State::Error);
        releaseLock();
    }
}

void NodeManager::onProcessOutput()
{
    QByteArray output = m_process->readAllStandardOutput();
    QString text = QString::fromUtf8(output);
    
    // Split into lines
    QStringList lines = text.split('\n', Qt::SkipEmptyParts);
    
    for (const QString& line : lines) {
        // Add to buffer with deduplication
        addLogLine(line);
        
        // Check for degradation patterns
        if (detectDegradationPattern(line) && !m_degradationDetected) {
            m_degradationDetected = true;
            if (m_state == State::RpcReady || m_state == State::Healthy) {
                setState(State::Degraded);
                emit nodeDegraded(m_degradationReason);
                
                // Slow down sync checks when degraded
                m_syncCheckTimer->setInterval(SYNC_CHECK_DEGRADED_INTERVAL);
            }
        }
    }
    
    // Emit deduplicated logs
    QStringList deduped = getDeduplicatedLogs(50);  // Last 50 deduplicated lines
    if (!deduped.isEmpty()) {
        emit logLinesAvailable(deduped);
    }
}

void NodeManager::onHealthCheckTimeout()
{
    m_healthCheckAttempts++;
    
    // Perform enhanced health check
    performHealthCheck();
}

void NodeManager::onSyncCheckTimeout()
{
    // Query sync status
    RpcReply* reply = m_rpcClient->getSyncStatus();
    connect(reply, &RpcReply::finished, this, [this, reply]() {
        reply->deleteLater();
        
        if (reply->error() == QNetworkReply::NoError) {
            QJsonDocument doc = QJsonDocument::fromJson(reply->readAll());
            QJsonObject obj = doc.object();
            QJsonObject result = obj["result"].toObject();
            
            bool syncing = result["syncing"].toBool(false);
            int currentBlock = result["currentBlock"].toInt(0);
            int highestBlock = result["highestBlock"].toInt(0);
            
            emit syncProgress(currentBlock, highestBlock, syncing);
        }
    });
}

// ==================== Private Methods ====================

void NodeManager::setState(State state)
{
    if (m_state != state) {
        m_state = state;
        qDebug() << "Node state changed to:" << static_cast<int>(state);
        
        // Map old "Running" references to appropriate new states
        // For backwards compatibility, any external code checking for "Running"
        // should now check for RpcReady, Healthy, or Degraded
        
        emit stateChanged(state);
    }
}

void NodeManager::setError(const QString& message)
{
    m_lastError = message;
    qWarning() << "Node error:" << message;
    emit error(message);
}

int NodeManager::findAvailablePort(int basePort, int range)
{
    for (int i = 0; i < range; i++) {
        int port = basePort + i;
        if (!isPortInUse(port)) {
            qDebug() << "Found available port:" << port;
            return port;
        }
    }
    return -1;
}

bool NodeManager::isPortInUse(int port)
{
    QTcpSocket socket;
    socket.connectToHost("127.0.0.1", port);
    bool inUse = socket.waitForConnected(100);
    socket.disconnectFromHost();
    return inUse;
}

bool NodeManager::acquireLock()
{
    QString lockPath = AppPaths::nodeLockFile();
    
    // Check if lock file already exists
    if (QFile::exists(lockPath)) {
        // Try to read PID from lock file
        QFile lockFile(lockPath);
        if (lockFile.open(QIODevice::ReadOnly)) {
            QString content = QString::fromUtf8(lockFile.readAll());
            lockFile.close();
            
            qWarning() << "Lock file exists with content:" << content;
            return false;
        }
    }
    
    // Create lock file
    m_lockFile = new QFile(lockPath, this);
    if (!m_lockFile->open(QIODevice::WriteOnly | QIODevice::Truncate)) {
        qWarning() << "Failed to create lock file:" << lockPath;
        delete m_lockFile;
        m_lockFile = nullptr;
        return false;
    }
    
    // Write PID to lock file
    QTextStream out(m_lockFile);
    out << "PID=" << QCoreApplication::applicationPid() << "\n";
    out << "Timestamp=" << QDateTime::currentDateTime().toString(Qt::ISODate) << "\n";
    m_lockFile->flush();
    
    qDebug() << "Acquired lock file:" << lockPath;
    return true;
}

void NodeManager::releaseLock()
{
    if (m_lockFile) {
        QString lockPath = m_lockFile->fileName();
        m_lockFile->close();
        m_lockFile->remove();
        delete m_lockFile;
        m_lockFile = nullptr;
        qDebug() << "Released lock file:" << lockPath;
    }
}

void NodeManager::writeNodeInfo()
{
    QString infoPath = AppPaths::nodeInfoFile();
    QFile file(infoPath);
    
    if (!file.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
        qWarning() << "Failed to write node info file:" << infoPath;
        return;
    }
    
    file.write(m_nodeInfo.toJson().toUtf8());
    file.close();
    
    qDebug() << "Wrote node info to:" << infoPath;
}

void NodeManager::startHealthCheck()
{
    m_healthCheckTimer->start();
}

void NodeManager::stopHealthCheck()
{
    m_healthCheckTimer->stop();
}

void NodeManager::startSyncMonitoring()
{
    m_syncCheckTimer->start();
}

void NodeManager::stopSyncMonitoring()
{
    m_syncCheckTimer->stop();
}

QString NodeManager::findBundledPython()
{
    // Get the application directory
    QString appDir = QCoreApplication::applicationDirPath();
    QStringList candidates;

#ifdef Q_OS_MACOS
    candidates << appDir + "/../Resources/node/venv/bin/python";
#elif defined(Q_OS_WIN)
    candidates << appDir + "/node/venv/Scripts/python.exe";
#else
    candidates << appDir + "/node/venv/bin/python";
    candidates << appDir + "/../lib/node/venv/bin/python";
    candidates << appDir + "/../lib/animica-wallet/node/venv/bin/python";
    candidates << "/usr/lib/animica-wallet/node/venv/bin/python";
#endif

    for (const QString& candidate : candidates) {
        QFileInfo bundledInfo(candidate);
        if (bundledInfo.exists() && bundledInfo.isExecutable()) {
            qDebug() << "Found bundled Python:" << candidate;
            return bundledInfo.absoluteFilePath();
        }
    }

    qDebug() << "Bundled Python not found in any known runtime location";
    return QString();
}

QString NodeManager::findPython()
{
    // First, try to find bundled Python
    QString bundled = findBundledPython();
    if (!bundled.isEmpty()) {
        return bundled;
    }
    
    // Fall back to system Python
    qDebug() << "Falling back to system Python";
    
    // Try to find Python 3 in common locations
    QStringList candidates = {
        "python3",
        "python",
        "/usr/bin/python3",
        "/usr/local/bin/python3",
        QStandardPaths::findExecutable("python3"),
        QStandardPaths::findExecutable("python")
    };
    
    for (const QString& candidate : candidates) {
        if (candidate.isEmpty()) continue;
        
        QString absPath = QStandardPaths::findExecutable(candidate);
        if (!absPath.isEmpty()) {
            // Verify it's Python 3
            QProcess check;
            check.start(absPath, {"--version"});
            if (check.waitForFinished(3000)) {
                QString output = QString::fromUtf8(check.readAllStandardOutput());
                if (output.contains("Python 3")) {
                    qDebug() << "Found Python:" << absPath << output.trimmed();
                    return absPath;
                }
            }
        }
    }
    
    qWarning() << "Python 3 not found in common locations";
    return QString();
}

// ==================== New Helper Methods ====================

bool NodeManager::detectDegradationPattern(const QString& line)
{
    // Detect known problematic patterns
    if (line.contains("UnboundLocalError: cannot access local variable 'asyncio'")) {
        m_degradationReason = "Node Python error: asyncio variable issue";
        qWarning() << "Detected degradation pattern:" << m_degradationReason;
        return true;
    }
    
    if (line.contains("NoneType") && line.contains("'>='")) {
        m_degradationReason = "Node snapshot orchestrator error: NoneType comparison";
        qWarning() << "Detected degradation pattern:" << m_degradationReason;
        return true;
    }
    
    if (line.contains("sync: reset cursor due to missing head_hash in db")) {
        m_degradationReason = "P2P sync error: missing head_hash (DB may be corrupt)";
        qWarning() << "Detected degradation pattern:" << m_degradationReason;
        return true;
    }
    
    if (line.contains("Connection refused") && line.contains("seed")) {
        // Don't mark as degraded for connection refused - it's expected if no seeds available
        qDebug() << "Seed connection failed (this is not critical)";
        return false;
    }
    
    return false;
}

void NodeManager::addLogLine(const QString& line)
{
    QDateTime now = QDateTime::currentDateTime();
    
    // Check if this line is a duplicate within the dedupe window
    if (m_logDedupeMap.contains(line)) {
        auto& entry = m_logDedupeMap[line];
        qint64 msSinceLastSeen = entry.second.msecsTo(now);
        
        if (msSinceLastSeen < LOG_DEDUPE_WINDOW_MS) {
            // Increment count
            entry.first++;
            entry.second = now;
            return;  // Don't add duplicate
        } else {
            // Outside dedupe window, reset
            entry.first = 1;
            entry.second = now;
        }
    } else {
        // New line
        m_logDedupeMap[line] = qMakePair(1, now);
    }
    
    // Add to ring buffer
    m_logBuffer.append(line);
    
    // Maintain buffer size
    while (m_logBuffer.size() > MAX_LOG_BUFFER_SIZE) {
        m_logBuffer.removeFirst();
    }
    
    // Clean up old dedupe entries (older than window)
    QStringList toRemove;
    for (auto it = m_logDedupeMap.begin(); it != m_logDedupeMap.end(); ++it) {
        if (it.value().second.msecsTo(now) > LOG_DEDUPE_WINDOW_MS * 2) {
            toRemove.append(it.key());
        }
    }
    for (const QString& key : toRemove) {
        m_logDedupeMap.remove(key);
    }
}

QStringList NodeManager::getDeduplicatedLogs(int maxLines)
{
    QStringList result;
    int count = qMin(maxLines, m_logBuffer.size());
    int start = qMax(0, m_logBuffer.size() - count);
    
    for (int i = start; i < m_logBuffer.size(); i++) {
        const QString& line = m_logBuffer[i];
        
        // Check if this line has been repeated
        if (m_logDedupeMap.contains(line)) {
            int repeatCount = m_logDedupeMap[line].first;
            if (repeatCount > 1) {
                result.append(QString("%1 (repeated %2 times)").arg(line).arg(repeatCount));
            } else {
                result.append(line);
            }
        } else {
            result.append(line);
        }
    }
    
    return result;
}

int NodeManager::calculateRestartDelay()
{
    // Exponential backoff: 1s, 2s, 4s, 8s, 16s, 32s, 60s (max)
    int baseDelay = 1000 * (1 << qMin(m_restartAttempts, 5));  // Cap at 2^5 = 32 seconds
    if (baseDelay > MAX_RESTART_DELAY_MS) {
        baseDelay = MAX_RESTART_DELAY_MS;
    }
    
    // Add jitter (±20%)
    int jitter = (QRandomGenerator::global()->bounded(baseDelay / 5)) - (baseDelay / 10);
    int delay = baseDelay + jitter;
    
    qDebug() << "Restart delay calculated:" << delay << "ms (attempt" << m_restartAttempts << ")";
    return delay;
}

void NodeManager::scheduleRestart()
{
    if (m_restartTimer->isActive()) {
        qDebug() << "Restart already scheduled";
        return;
    }
    
    int delay = calculateRestartDelay();
    m_restartAttempts++;
    
    qDebug() << "Scheduling restart in" << delay << "ms";
    m_restartTimer->start(delay);
}

void NodeManager::onRestartBackoffTimeout()
{
    qDebug() << "Restart backoff timeout, restarting node";
    startNode(m_currentNetwork);
}

void NodeManager::performHealthCheck()
{
    // Try to get chain head as a more robust health check
    RpcReply* reply = m_rpcClient->getHead();
    connect(reply, &RpcReply::finished, this, [this, reply]() {
        reply->deleteLater();
        
        if (reply->error() == QNetworkReply::NoError) {
            QJsonDocument doc = QJsonDocument::fromJson(reply->readAll());
            QJsonObject obj = doc.object();
            
            if (obj.contains("result")) {
                QJsonObject result = obj["result"].toObject();
                
                // Check if we can read basic chain info
                bool hasHeight = result.contains("height") || result.contains("number");
                
                if (hasHeight) {
                    qDebug() << "Node RPC is ready (chain head accessible)";
                    stopHealthCheck();
                    
                    // Transition to RpcReady, then check if fully healthy
                    setState(State::RpcReady);
                    emit nodeReady();
                    
                    // If no degradation detected, move to Healthy
                    if (!m_degradationDetected) {
                        setState(State::Healthy);
                    }
                    
                    // Start sync monitoring
                    startSyncMonitoring();
                    return;
                }
            }
        }
        
        // Still waiting for RPC readiness
        if (m_healthCheckAttempts < HEALTH_CHECK_MAX_ATTEMPTS) {
            // Continue checking
            if (m_healthCheckAttempts > 30) {
                // After 30 attempts (7.5s), slow down checks
                m_healthCheckTimer->setInterval(HEALTH_CHECK_BACKOFF_INTERVAL);
            }
        } else {
            // After max attempts, mark as degraded but keep trying
            qWarning() << "RPC not ready after" << m_healthCheckAttempts << "attempts, marking as degraded";
            m_degradationDetected = true;
            m_degradationReason = "RPC endpoint not responding within 30 seconds";
            setState(State::Degraded);
            emit nodeDegraded(m_degradationReason);
            
            // Keep checking with slower interval
            m_healthCheckTimer->setInterval(HEALTH_CHECK_BACKOFF_INTERVAL);
        }
    });
}

bool NodeManager::ensureDataDirValid(int chainId)
{
    QString dataDir;
    if (m_dataDirManager) {
        dataDir = m_dataDirManager->getChainDataDir(chainId);
    } else {
        dataDir = AppPaths::nodeChainDir(chainId);
    }
    
    // Ensure directory exists
    QDir dir(dataDir);
    if (!dir.exists()) {
        if (!dir.mkpath(".")) {
            qWarning() << "Failed to create data directory:" << dataDir;
            return false;
        }
        qDebug() << "Created data directory:" << dataDir;
    }
    
    // Check write permissions
    QFileInfo dirInfo(dataDir);
    if (!dirInfo.isWritable()) {
        qWarning() << "Data directory not writable:" << dataDir;
        return false;
    }
    
    qDebug() << "Data directory valid:" << dataDir;
    return true;
}

bool NodeManager::resetChainData(int chainId)
{
    if (m_state != State::Stopped) {
        qWarning() << "Cannot reset chain data while node is running";
        return false;
    }
    
    QString dataDir;
    if (m_dataDirManager) {
        dataDir = m_dataDirManager->getChainDataDir(chainId);
    } else {
        dataDir = AppPaths::nodeChainDir(chainId);
    }
    
    QDir dir(dataDir);
    if (!dir.exists()) {
        qDebug() << "Data directory does not exist, nothing to reset";
        return true;
    }
    
    // Remove all files in the directory
    qDebug() << "Resetting chain data in:" << dataDir;
    if (!dir.removeRecursively()) {
        qWarning() << "Failed to remove data directory";
        return false;
    }
    
    // Recreate the directory
    if (!dir.mkpath(".")) {
        qWarning() << "Failed to recreate data directory";
        return false;
    }
    
    qDebug() << "Chain data reset successfully";
    return true;
}

// ==================== NodeInfo Methods ====================

QString NodeManager::NodeInfo::toJson() const
{
    QJsonObject obj;
    obj["pid"] = pid;
    obj["rpcPort"] = rpcPort;
    obj["p2pPort"] = p2pPort;
    obj["network"] = network;
    obj["pythonPath"] = pythonPath;
    obj["startTime"] = startTime.toString(Qt::ISODate);
    obj["version"] = version;
    
    QJsonDocument doc(obj);
    return QString::fromUtf8(doc.toJson(QJsonDocument::Indented));
}

NodeManager::NodeInfo NodeManager::NodeInfo::fromJson(const QString& json)
{
    NodeInfo info;
    QJsonDocument doc = QJsonDocument::fromJson(json.toUtf8());
    QJsonObject obj = doc.object();
    
    info.pid = obj["pid"].toInt();
    info.rpcPort = obj["rpcPort"].toInt();
    info.p2pPort = obj["p2pPort"].toInt();
    info.network = obj["network"].toString();
    info.pythonPath = obj["pythonPath"].toString();
    info.startTime = QDateTime::fromString(obj["startTime"].toString(), Qt::ISODate);
    info.version = obj["version"].toString();
    
    return info;
}

void NodeManager::setDataDirManager(DataDirManager* dataDirManager)
{
    m_dataDirManager = dataDirManager;
}
