#include "AnimicaWalletBackend.h"

#include <QCoreApplication>
#include <QDir>
#include <QFileInfo>
#include <QJsonDocument>
#include <QProcess>
#include <QProcessEnvironment>
#include <QStandardPaths>

namespace {
QStringList bundledPythonCandidates()
{
    QStringList candidates;
    const QString appDir = QCoreApplication::applicationDirPath();
    if (appDir.isEmpty()) {
        return candidates;
    }

#ifdef Q_OS_WIN
    candidates << QDir(appDir).filePath("node/venv/Scripts/python.exe");
    candidates << QDir(appDir).absoluteFilePath("../node/venv/Scripts/python.exe");
#elif defined(Q_OS_MACOS)
    candidates << QDir(appDir).absoluteFilePath("../Resources/node/venv/bin/python");
    candidates << QDir(appDir).filePath("node/venv/bin/python");
    candidates << QDir(appDir).absoluteFilePath("../animica-node/venv/bin/python");
#else
    candidates << QDir(appDir).filePath("node/venv/bin/python");
    candidates << QDir(appDir).absoluteFilePath("../animica-node/venv/bin/python");
    candidates << QDir(appDir).absoluteFilePath("../lib/animica-wallet/node/venv/bin/python");
    candidates << QDir(appDir).absoluteFilePath("../lib/node/venv/bin/python");

    const QDir libRoot(QDir(appDir).absoluteFilePath("../lib"));
    if (libRoot.exists()) {
        const QFileInfoList entries = libRoot.entryInfoList(
            QDir::Dirs | QDir::NoDotAndDotDot,
            QDir::Name
        );
        for (const QFileInfo& entry : entries) {
            if (entry.fileName().endsWith("-linux-gnu")) {
                candidates << QDir(entry.absoluteFilePath()).filePath("animica-wallet/node/venv/bin/python");
            }
        }
    }
#endif

    return candidates;
}

QStringList repoPythonCandidates()
{
    QStringList candidates;
    const QString repoRoot = AnimicaWalletBackend::findRepoRoot();
    if (repoRoot.isEmpty()) {
        return candidates;
    }
#ifdef Q_OS_WIN
    candidates << QDir(repoRoot).filePath(".venv/Scripts/python.exe");
#else
    candidates << QDir(repoRoot).filePath(".venv/bin/python");
#endif
    return candidates;
}

QStringList existingPyPath(const QProcessEnvironment& env)
{
    const QString sep =
#ifdef Q_OS_WIN
        ";";
#else
        ":";
#endif
    return env.value("PYTHONPATH").split(sep, Qt::SkipEmptyParts);
}

QString joinPyPath(const QStringList& paths)
{
#ifdef Q_OS_WIN
    return paths.join(';');
#else
    return paths.join(':');
#endif
}

} // namespace

AnimicaWalletBackend::AnimicaWalletBackend(QObject* parent)
    : QObject(parent)
{
}

void AnimicaWalletBackend::setWalletFile(const QString& walletFile)
{
    m_walletFile = walletFile;
}

void AnimicaWalletBackend::setRpcUrl(const QString& rpcUrl)
{
    m_rpcUrl = rpcUrl;
}

void AnimicaWalletBackend::setExplorerUrl(const QString& explorerUrl)
{
    m_explorerUrl = explorerUrl;
}

QJsonObject AnimicaWalletBackend::errorResponse(const QString& message, const QString& details)
{
    QJsonObject error;
    error["message"] = message;
    if (!details.isEmpty()) {
        error["details"] = details;
    }
    QJsonObject response;
    response["ok"] = false;
    response["error"] = error;
    return response;
}

QString AnimicaWalletBackend::findRepoRoot()
{
    const QString override = qEnvironmentVariable("ANIMICA_REPO_ROOT");
    if (!override.isEmpty()) {
        const QFileInfo info(override);
        if (info.exists() && info.isDir()) {
            QDir dir(info.absoluteFilePath());
            if (dir.exists("python/animica/qt_wallet_bridge.py") && dir.exists("sdk/python")) {
                return dir.absolutePath();
            }
        }
    }

    QStringList seeds;
    seeds << QDir::currentPath() << QCoreApplication::applicationDirPath();

    for (const QString& seed : seeds) {
        QDir dir(seed);
        for (int i = 0; i < 8; ++i) {
            if (dir.exists("python/animica/qt_wallet_bridge.py") && dir.exists("sdk/python")) {
                return dir.absolutePath();
            }
            if (!dir.cdUp()) {
                break;
            }
        }
    }
    return QString();
}

QString AnimicaWalletBackend::findPythonInterpreter()
{
    const QString override = qEnvironmentVariable("ANIMICA_WALLET_PYTHON");
    if (!override.isEmpty()) {
        QFileInfo info(override);
        if (info.exists() && info.isExecutable()) {
            return info.absoluteFilePath();
        }
    }

    for (const QString& candidate : bundledPythonCandidates()) {
        QFileInfo info(candidate);
        if (info.exists() && info.isExecutable()) {
            return info.absoluteFilePath();
        }
    }

    for (const QString& candidate : repoPythonCandidates()) {
        QFileInfo info(candidate);
        if (info.exists() && info.isExecutable()) {
            return info.absoluteFilePath();
        }
    }

    const QString python3 = QStandardPaths::findExecutable("python3");
    if (!python3.isEmpty()) {
        return python3;
    }
    return QStandardPaths::findExecutable("python");
}

QJsonObject AnimicaWalletBackend::call(const QString& operation, const QJsonObject& args, int timeoutMs) const
{
    m_lastError.clear();

    const QString python = findPythonInterpreter();
    if (python.isEmpty()) {
        m_lastError = "No Python interpreter was found for the Animica wallet backend.";
        return errorResponse(m_lastError);
    }

    QJsonObject payload;
    payload["op"] = operation;

    QJsonObject mergedArgs = args;
    if (!m_walletFile.isEmpty() && !mergedArgs.contains("wallet_file")) {
        mergedArgs["wallet_file"] = m_walletFile;
    }
    if (!m_rpcUrl.isEmpty() && !mergedArgs.contains("rpc_url")) {
        mergedArgs["rpc_url"] = m_rpcUrl;
    }
    if (!m_explorerUrl.isEmpty() && !mergedArgs.contains("explorer_url")) {
        mergedArgs["explorer_url"] = m_explorerUrl;
    }
    payload["args"] = mergedArgs;

    QProcess process;
    QProcessEnvironment env = QProcessEnvironment::systemEnvironment();
    const QString repoRoot = findRepoRoot();
    if (!repoRoot.isEmpty()) {
        QStringList pyPath = existingPyPath(env);
        pyPath.prepend(QDir(repoRoot).filePath("sdk/python"));
        pyPath.prepend(QDir(repoRoot).filePath("python"));
        env.insert("PYTHONPATH", joinPyPath(pyPath));
    }
    process.setProcessEnvironment(env);
    process.start(python, {"-m", "animica.qt_wallet_bridge"});
    if (!process.waitForStarted(5000)) {
        m_lastError = "Failed to start the Animica wallet backend bridge.";
        return errorResponse(m_lastError, process.errorString());
    }

    const QByteArray request = QJsonDocument(payload).toJson(QJsonDocument::Compact);
    process.write(request);
    process.closeWriteChannel();

    if (!process.waitForFinished(timeoutMs)) {
        process.kill();
        process.waitForFinished(1000);
        m_lastError = "Timed out waiting for the Animica wallet backend.";
        return errorResponse(m_lastError);
    }

    const QByteArray stdoutData = process.readAllStandardOutput().trimmed();
    const QByteArray stderrData = process.readAllStandardError().trimmed();
    QJsonParseError parseError;
    const QJsonDocument responseDoc = QJsonDocument::fromJson(stdoutData, &parseError);
    if (parseError.error != QJsonParseError::NoError || !responseDoc.isObject()) {
        const QString details = !stderrData.isEmpty()
            ? QString::fromUtf8(stderrData)
            : QString::fromUtf8(stdoutData);
        m_lastError = "The Animica wallet backend returned an invalid response.";
        return errorResponse(m_lastError, details);
    }

    QJsonObject response = responseDoc.object();
    if (!response.value("ok").toBool()) {
        m_lastError = response.value("error").toObject().value("message").toString("Wallet backend operation failed.");
    }
    return response;
}
