#ifndef CONSOLEEXECUTOR_H
#define CONSOLEEXECUTOR_H

#include <QObject>
#include <QString>
#include <QJsonValue>

class AnimicaRpcClient;

/**
 * @brief Safe command execution engine for diagnostics console.
 * 
 * Executes CLI commands and RPC calls with:
 * - Timeouts (5-60 seconds depending on operation)
 * - Output limits (2MB, 20k lines)
 * - Automatic redaction of sensitive data
 * - Structured result format
 * 
 * Prefers RPC over CLI for speed and structured output.
 */
class ConsoleExecutor : public QObject
{
    Q_OBJECT

public:
    struct ExecutionResult {
        bool success;
        QString output;
        QString error;
        int exitCode;
        bool timedOut;
        bool truncated;
        qint64 durationMs;
    };

    explicit ConsoleExecutor(AnimicaRpcClient* rpcClient, QObject* parent = nullptr);

    /**
     * @brief Execute command (CLI or RPC).
     * @param command Full command string (e.g., "node status" or "rpc call node.getStatus")
     * @param timeoutMs Timeout in milliseconds (0 = use default)
     * @return Execution result with output and status
     */
    ExecutionResult execute(const QString& command, int timeoutMs = 0);

    /**
     * @brief Execute RPC method directly.
     * @param method RPC method name
     * @param params RPC parameters
     * @param timeoutMs Timeout in milliseconds (0 = use default)
     * @return Execution result with JSON output
     */
    ExecutionResult executeRpc(const QString& method, const QJsonValue& params = QJsonValue(), int timeoutMs = 0);

    /**
     * @brief Set maximum output size (bytes).
     */
    void setMaxOutputSize(qint64 bytes) { m_maxOutputSize = bytes; }

    /**
     * @brief Set maximum output lines.
     */
    void setMaxOutputLines(int lines) { m_maxOutputLines = lines; }

private:
    QString formatJsonOutput(const QString& jsonText);
    QString applyOutputLimits(const QString& output, bool& truncated);
    int getDefaultTimeout(const QString& command);

    AnimicaRpcClient* m_rpcClient;
    qint64 m_maxOutputSize;      // 2MB default
    int m_maxOutputLines;         // 20k lines default
};

#endif // CONSOLEEXECUTOR_H
