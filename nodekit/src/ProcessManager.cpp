#include "AnimicaNodeKit/ProcessManager.h"

#include <QFileInfo>
#include <QFile>
#include <QDir>
#include <QStandardPaths>
#include <QTextStream>

namespace animica::nodekit {

ProcessManager::ProcessManager(QObject *parent) : QObject(parent) {
    connect(&process_, &QProcess::readyReadStandardOutput, this, &ProcessManager::handleReadyRead);
    connect(&process_, &QProcess::readyReadStandardError, this, &ProcessManager::handleReadyRead);
    connect(&process_, &QProcess::finished, this, &ProcessManager::handleProcessFinished);
}

void ProcessManager::configure(const NodeKitConfig &config) {
    config_ = config;
    status_.rpcPort = config.rpcPort;
    status_.wsPort = config.wsPort;
    status_.metricsPort = config.metricsPort;
}

bool ProcessManager::start() {
    if (process_.state() != QProcess::NotRunning) {
        return true;
    }

    if (config_.useDockerMode) {
        composePath_ = buildComposePath();
        if (composePath_.isEmpty()) {
            appendLogLine(QStringLiteral("Failed to create docker compose file."));
            emit nodeCrashed(QStringLiteral("compose file error"));
            return false;
        }
        process_.setProgram(QStringLiteral("docker"));
        process_.setArguments(buildDockerArgs(composePath_));
        process_.setWorkingDirectory(config_.dataDir);
        process_.start();
        if (!process_.waitForStarted(5000)) {
            appendLogLine(QStringLiteral("Failed to start docker compose."));
            emit nodeCrashed(QStringLiteral("docker start failed"));
            return false;
        }
        status_.pid = process_.processId();
        status_.startedAt = QDateTime::currentDateTimeUtc();
        emit nodeStarted();
        return true;
    }

    const QString nodeBinary = resolveNodeBinary();
    if (nodeBinary.isEmpty()) {
        appendLogLine(QStringLiteral("Animica node binary not found."));
        emit nodeCrashed(QStringLiteral("node binary not found"));
        return false;
    }

    process_.setProgram(nodeBinary);
    process_.setArguments(buildNodeArgs());
    process_.setWorkingDirectory(config_.dataDir);
    process_.start();

    if (!process_.waitForStarted(5000)) {
        appendLogLine(QStringLiteral("Failed to start node process."));
        emit nodeCrashed(QStringLiteral("failed to start"));
        return false;
    }

    status_.pid = process_.processId();
    status_.startedAt = QDateTime::currentDateTimeUtc();
    emit nodeStarted();
    return true;
}

void ProcessManager::stop() {
    if (process_.state() == QProcess::NotRunning) {
        return;
    }

    if (config_.useDockerMode && !composePath_.isEmpty()) {
        QProcess stopProcess;
        stopProcess.setProgram(QStringLiteral("docker"));
        stopProcess.setArguments(QStringList() << "compose" << "-f" << composePath_ << "down");
        stopProcess.setWorkingDirectory(config_.dataDir);
        stopProcess.start();
        stopProcess.waitForFinished(10000);
        process_.kill();
        process_.waitForFinished(3000);
    } else {
        process_.terminate();
        if (!process_.waitForFinished(5000)) {
            process_.kill();
            process_.waitForFinished(3000);
        }
    }
    status_.pid = -1;
    emit nodeStopped();
}

void ProcessManager::restart() {
    stop();
    start();
}

QStringList ProcessManager::tailLogs(int nLines) const {
    if (nLines <= 0) {
        return {};
    }

    const int start = qMax(0, logBuffer_.size() - nLines);
    return logBuffer_.mid(start);
}

NodeStatus ProcessManager::status() const {
    return status_;
}

bool ProcessManager::isRunning() const {
    return process_.state() != QProcess::NotRunning;
}

void ProcessManager::handleReadyRead() {
    const QString output = QString::fromUtf8(process_.readAllStandardOutput());
    const QString err = QString::fromUtf8(process_.readAllStandardError());
    const QString combined = output + err;
    const QStringList lines = combined.split('\n', Qt::SkipEmptyParts);
    for (const QString &line : lines) {
        appendLogLine(line.trimmed());
    }
}

void ProcessManager::handleProcessFinished(int exitCode, QProcess::ExitStatus status) {
    Q_UNUSED(exitCode)
    if (status == QProcess::CrashExit) {
        emit nodeCrashed(QStringLiteral("node crashed"));
    }
    emit nodeStopped();
}

QStringList ProcessManager::buildNodeArgs() const {
    QStringList args;
    args << "node" << "run";
    args << "--rpc-host" << "127.0.0.1";
    args << "--rpc-port" << QString::number(config_.rpcPort);
    args << "--p2p-port" << QString::number(config_.metricsPort > 0 ? config_.metricsPort : 0);
    args << "--datadir" << config_.dataDir;
    if (config_.enableWebSocket && config_.wsPort > 0) {
        args << "--ws-host" << "127.0.0.1";
        args << "--ws-port" << QString::number(config_.wsPort);
    }
    return args;
}

QString ProcessManager::buildComposePath() const {
    const QString composePath = QDir(config_.dataDir).filePath(QStringLiteral("docker-compose.nodekit.yml"));
    QFile file(composePath);
    if (!file.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
        return {};
    }
    QTextStream stream(&file);
    stream << "services:\n";
    stream << "  animica-node:\n";
    stream << "    image: animica/node:latest\n";
    stream << "    command: [\"node\", \"run\", \"--rpc-host\", \"127.0.0.1\", \"--rpc-port\", \"" << config_.rpcPort << "\", \"--datadir\", \"/data\"]\n";
    stream << "    volumes:\n";
    stream << "      - " << config_.dataDir << ":/data\n";
    stream << "    ports:\n";
    stream << "      - \"127.0.0.1:" << config_.rpcPort << ":" << config_.rpcPort << "\"\n";
    file.close();
    return composePath;
}

QStringList ProcessManager::buildDockerArgs(const QString &composePath) const {
    return QStringList() << "compose" << "-f" << composePath << "up";
}

QString ProcessManager::resolveNodeBinary() const {
    const QString envPath = qEnvironmentVariable("ANIMICA_NODE_PATH");
    if (!envPath.isEmpty() && QFileInfo::exists(envPath)) {
        return envPath;
    }

    const QString appPath = QStandardPaths::findExecutable("animica");
    if (!appPath.isEmpty()) {
        return appPath;
    }

    return {};
}

void ProcessManager::appendLogLine(const QString &line) {
    if (line.isEmpty()) {
        return;
    }
    logBuffer_.append(line);
    if (logBuffer_.size() > maxLogLines_) {
        logBuffer_.removeFirst();
    }
    emit logUpdated(line);
}

} // namespace animica::nodekit
