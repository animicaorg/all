#include "NodeManager.h"
#include "../platform/AppPaths.h"
#include "../platform/DataDirManager.h"
#include <QStandardPaths>
#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkReply>
#include <QDesktopServices>
#include <QFileInfo>
#include <QTextStream>
#include <QTcpSocket>
#include <QDebug>

NodeManager::NodeManager(QObject* parent)
    : QObject(parent)
    , m_state(State::Stopped)
    , m_process(new QProcess(this))
    , m_rpcClient(new AnimicaRpcClient(this))
    , m_dataDirManager(nullptr)
    , m_healthCheckTimer(new QTimer(this))
    , m_syncCheckTimer(new QTimer(this))
    , m_healthCheckAttempts(0)
    , m_lockFile(nullptr)
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
    m_healthCheckTimer->setInterval(HEALTH_CHECK_TIMEOUT);
    connect(m_healthCheckTimer, &QTimer::timeout, this, &NodeManager::onHealthCheckTimeout);
    
    m_syncCheckTimer->setSingleShot(false);
    m_syncCheckTimer->setInterval(SYNC_CHECK_INTERVAL);
    connect(m_syncCheckTimer, &QTimer::timeout, this, &NodeManager::onSyncCheckTimeout);
    
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
    if (m_state == State::Running || m_state == State::Starting) {
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
    
    // Check for existing lock
    if (!acquireLock()) {
        setError("Node is already running (lock file exists)");
        setState(State::Error);
        return false;
    }
    
    // Find available ports
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
        case State::Running: out << "Running"; break;
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
    
    // Split into lines and emit
    QStringList lines = text.split('\n', Qt::SkipEmptyParts);
    if (!lines.isEmpty()) {
        emit logLinesAvailable(lines);
    }
    
    // Log to console
    for (const QString& line : lines) {
        qDebug().noquote() << "[Node]" << line;
    }
}

void NodeManager::onHealthCheckTimeout()
{
    m_healthCheckAttempts++;
    
    // Send ping request
    QNetworkReply* reply = m_rpcClient->ping();
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        
        if (reply->error() == QNetworkReply::NoError) {
            // Parse response
            QJsonDocument doc = QJsonDocument::fromJson(reply->readAll());
            QJsonObject obj = doc.object();
            
            if (obj.contains("result") && obj["result"].toString() == "pong") {
                qDebug() << "Node is ready (ping successful)";
                stopHealthCheck();
                setState(State::Running);
                emit nodeReady();
                
                // Start sync monitoring
                startSyncMonitoring();
            } else {
                qWarning() << "Unexpected ping response:" << obj;
            }
        } else if (m_healthCheckAttempts >= HEALTH_CHECK_MAX_ATTEMPTS) {
            qWarning() << "Health check failed after" << HEALTH_CHECK_MAX_ATTEMPTS << "attempts";
            setError("Node failed to become ready (timeout)");
            stopNode();
        }
    });
}

void NodeManager::onSyncCheckTimeout()
{
    // Query sync status
    QNetworkReply* reply = m_rpcClient->getSyncStatus();
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
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
    QString bundledPython;
    
#ifdef Q_OS_MACOS
    // macOS: AnimicaWallet.app/Contents/Resources/node/venv/bin/python
    bundledPython = appDir + "/../Resources/node/venv/bin/python";
#elif defined(Q_OS_WIN)
    // Windows: <exe_dir>/node/venv/Scripts/python.exe
    bundledPython = appDir + "/node/venv/Scripts/python.exe";
#else
    // Linux: <exe_dir>/node/venv/bin/python
    bundledPython = appDir + "/node/venv/bin/python";
#endif
    
    QFileInfo bundledInfo(bundledPython);
    if (bundledInfo.exists() && bundledInfo.isExecutable()) {
        qDebug() << "Found bundled Python:" << bundledPython;
        return bundledInfo.absoluteFilePath();
    }
    
    qDebug() << "Bundled Python not found at:" << bundledPython;
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
