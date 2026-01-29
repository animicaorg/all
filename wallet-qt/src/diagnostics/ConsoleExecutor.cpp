#include "ConsoleExecutor.h"
#include "Redactor.h"
#include "../rpc/AnimicaRpcClient.h"
#include "../platform/AppPaths.h"
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>
#include <QEventLoop>
#include <QTimer>
#include <QElapsedTimer>
#include <QNetworkReply>

ConsoleExecutor::ConsoleExecutor(AnimicaRpcClient* rpcClient, QObject* parent)
    : QObject(parent)
    , m_rpcClient(rpcClient)
    , m_maxOutputSize(2 * 1024 * 1024)  // 2MB
    , m_maxOutputLines(20000)             // 20k lines
{
}

ConsoleExecutor::ExecutionResult ConsoleExecutor::execute(const QString& command, int timeoutMs)
{
    QElapsedTimer timer;
    timer.start();

    ExecutionResult result;
    result.success = false;
    result.exitCode = -1;
    result.timedOut = false;
    result.truncated = false;

    QString trimmed = command.trimmed();
    if (trimmed.isEmpty()) {
        result.error = "Empty command";
        result.durationMs = timer.elapsed();
        return result;
    }

    // Parse command
    QStringList parts = trimmed.split(QRegularExpression(R"(\s+)"));
    if (parts.isEmpty()) {
        result.error = "Invalid command";
        result.durationMs = timer.elapsed();
        return result;
    }

    // Check for RPC call command
    if (parts[0] == "rpc" && parts.size() >= 3 && parts[1] == "call") {
        QString method = parts[2];
        QJsonArray params;
        
        // Parse remaining parts as JSON params
        for (int i = 3; i < parts.size(); ++i) {
            QJsonDocument doc = QJsonDocument::fromJson(parts[i].toUtf8());
            if (!doc.isNull()) {
                params.append(doc.isArray() ? QJsonValue(doc.array()) : QJsonValue(doc.object()));
            } else {
                params.append(parts[i]);
            }
        }

        return executeRpc(method, params, timeoutMs);
    }

    // Otherwise execute as CLI command
    result = executeCli(parts, timeoutMs > 0 ? timeoutMs : getDefaultTimeout(trimmed));
    result.durationMs = timer.elapsed();
    
    return result;
}

ConsoleExecutor::ExecutionResult ConsoleExecutor::executeRpc(const QString& method, const QJsonValue& params, int timeoutMs)
{
    QElapsedTimer timer;
    timer.start();

    ExecutionResult result;
    result.success = false;
    result.exitCode = -1;
    result.timedOut = false;
    result.truncated = false;

    if (!m_rpcClient) {
        result.error = "RPC client not available";
        result.durationMs = timer.elapsed();
        return result;
    }

    // Execute RPC call
    QNetworkReply* reply = m_rpcClient->call(method, params);
    if (!reply) {
        result.error = "Failed to create RPC request";
        result.durationMs = timer.elapsed();
        return result;
    }

    // Wait for response with timeout
    QEventLoop loop;
    QTimer timeout;
    timeout.setSingleShot(true);
    
    int actualTimeout = timeoutMs > 0 ? timeoutMs : getDefaultTimeout(method);
    timeout.setInterval(actualTimeout);

    connect(reply, &QNetworkReply::finished, &loop, &QEventLoop::quit);
    connect(&timeout, &QTimer::timeout, &loop, &QEventLoop::quit);

    timeout.start();
    loop.exec();

    if (!timeout.isActive()) {
        // Timeout occurred
        result.timedOut = true;
        result.error = QString("RPC call timed out after %1ms").arg(actualTimeout);
        reply->abort();
        reply->deleteLater();
        result.durationMs = timer.elapsed();
        return result;
    }

    timeout.stop();

    // Check for errors
    if (reply->error() != QNetworkReply::NoError) {
        result.error = reply->errorString();
        reply->deleteLater();
        result.durationMs = timer.elapsed();
        return result;
    }

    // Parse response
    QByteArray data = reply->readAll();
    reply->deleteLater();

    QString output = QString::fromUtf8(data);
    output = formatJsonOutput(output);
    output = applyOutputLimits(output, result.truncated);
    
    // Apply redaction
    result.output = Redactor::redact(output);
    result.success = true;
    result.exitCode = 0;
    result.durationMs = timer.elapsed();

    return result;
}

QString ConsoleExecutor::getAnimicaCliPath() const
{
#ifdef Q_OS_WIN
    QDir nodeDir(AppPaths::getBundledNodePath());
    return nodeDir.filePath("animica-node.bat");
#else
    QDir nodeDir(AppPaths::getBundledNodePath());
    return nodeDir.filePath("animica-node");
#endif
}

ConsoleExecutor::ExecutionResult ConsoleExecutor::executeCli(const QStringList& args, int timeoutMs)
{
    ExecutionResult result;
    result.success = false;
    result.exitCode = -1;
    result.timedOut = false;
    result.truncated = false;

    QString program = getAnimicaCliPath();
    
    QProcess process;
    process.setProgram(program);
    process.setArguments(args);
    process.setProcessChannelMode(QProcess::MergedChannels);

    // Start process
    process.start();
    if (!process.waitForStarted(5000)) {
        result.error = "Failed to start process: " + process.errorString();
        return result;
    }

    // Wait for completion with timeout
    if (!process.waitForFinished(timeoutMs)) {
        result.timedOut = true;
        result.error = QString("Process timed out after %1ms").arg(timeoutMs);
        process.kill();
        process.waitForFinished(1000);
        return result;
    }

    // Read output
    QString output = QString::fromUtf8(process.readAllStandardOutput());
    output = applyOutputLimits(output, result.truncated);
    
    // Apply redaction
    result.output = Redactor::redact(output);
    result.exitCode = process.exitCode();
    result.success = (result.exitCode == 0);

    if (!result.success && result.output.isEmpty()) {
        result.error = "Command failed with exit code " + QString::number(result.exitCode);
    }

    return result;
}

QString ConsoleExecutor::formatJsonOutput(const QString& jsonText)
{
    QJsonDocument doc = QJsonDocument::fromJson(jsonText.toUtf8());
    if (doc.isNull()) {
        return jsonText;
    }

    // Pretty-print JSON
    return QString::fromUtf8(doc.toJson(QJsonDocument::Indented));
}

QString ConsoleExecutor::applyOutputLimits(const QString& output, bool& truncated)
{
    truncated = false;

    // Check byte limit
    if (output.size() > m_maxOutputSize) {
        truncated = true;
        return output.left(m_maxOutputSize) + 
               "\n\n[Output truncated: exceeded " + 
               QString::number(m_maxOutputSize) + " byte limit]";
    }

    // Check line limit
    QStringList lines = output.split('\n');
    if (lines.size() > m_maxOutputLines) {
        truncated = true;
        QStringList truncatedLines = lines.mid(0, m_maxOutputLines);
        return truncatedLines.join('\n') + 
               "\n\n[Output truncated: exceeded " + 
               QString::number(m_maxOutputLines) + " line limit]";
    }

    return output;
}

int ConsoleExecutor::getDefaultTimeout(const QString& command)
{
    QString lower = command.toLower();

    // Bootstrap and snapshot operations: 60 seconds
    if (lower.contains("bootstrap") || lower.contains("snapshot")) {
        return 60000;
    }

    // Sync operations: 30 seconds
    if (lower.startsWith("sync.") || lower.contains("sync ")) {
        return 30000;
    }

    // Default: 5 seconds
    return 5000;
}
