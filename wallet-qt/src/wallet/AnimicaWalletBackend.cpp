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
    // Bundled relocatable CPython (python-build-standalone) keeps python.exe at
    // the venv root, not under Scripts/ — accept that layout too.
    candidates << QDir(appDir).filePath("node/venv/python.exe");
    candidates << QDir(appDir).absoluteFilePath("../node/venv/python.exe");
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

namespace {
// True if `path` is a real Python interpreter we may execute.
//
// Windows ships %LOCALAPPDATA%\Microsoft\WindowsApps\python.exe — an "app
// execution alias": a zero-byte reparse point that exists, reports itself
// executable, and on launch prints
//   "Python was not found; run without arguments to install from the Microsoft
//    Store, or disable this shortcut from Settings > Apps > ..."
// to stdout instead of running Python. QStandardPaths::findExecutable("python")
// happily returns it, so without this guard the bridge receives that sentence
// where JSON was expected and the wallet reports "the Animica wallet backend
// returned an invalid response". Skip the alias so we fall through to a real
// interpreter, or to the honest "no interpreter" path.
bool isUsablePythonExecutable(const QString& path)
{
    const QFileInfo info(path);
    if (!info.exists() || info.isDir() || !info.isExecutable()) {
        return false;
    }
#ifdef Q_OS_WIN
    const QString abs = QDir::fromNativeSeparators(info.absoluteFilePath());
    if (abs.contains(QLatin1String("/Microsoft/WindowsApps/"), Qt::CaseInsensitive)) {
        return false;
    }
    if (info.size() == 0) {
        return false;
    }
#endif
    return true;
}
} // namespace

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

namespace {
// True if `python -c "import animica"` exits 0 — i.e. the interpreter can serve
// the wallet bridge. Quick (2s) and side-effect free.
bool pythonHasAnimica(const QString& python)
{
    QProcess p;
    p.start(python, {"-c", "import animica"});
    if (!p.waitForFinished(4000)) {
        p.kill();
        p.waitForFinished(500);
        return false;
    }
    return p.exitStatus() == QProcess::NormalExit && p.exitCode() == 0;
}

// Common places a pip-installed `animica` might live on macOS/Linux when the
// app wasn't shipped with its own bundled venv (e.g. cross-built bundles).
QStringList systemPythonCandidates()
{
    QStringList c;
    const QString home = QDir::homePath();
    // Homebrew (Apple silicon + Intel) and common Python.org / system paths.
    c << "/opt/homebrew/bin/python3" << "/usr/local/bin/python3" << "/usr/bin/python3";
    for (const QString& v : {"3.13", "3.12", "3.11", "3.10"}) {
        c << QString("/opt/homebrew/bin/python%1").arg(v);
        c << QString("/usr/local/bin/python%1").arg(v);
        c << QString("/Library/Frameworks/Python.framework/Versions/%1/bin/python3").arg(v);
    }
    // conda / miniforge / mambaforge and the active conda env.
    const QString condaPrefix = qEnvironmentVariable("CONDA_PREFIX");
    if (!condaPrefix.isEmpty())
        c << QDir(condaPrefix).filePath("bin/python");
    for (const QString& d : {"miniconda3", "anaconda3", "miniforge3", "mambaforge"})
        c << QDir(home).filePath(d + "/bin/python");
    // pyenv shims + active virtualenv.
    c << QDir(home).filePath(".pyenv/shims/python3");
    const QString venv = qEnvironmentVariable("VIRTUAL_ENV");
    if (!venv.isEmpty())
        c << QDir(venv).filePath("bin/python");
    // Finally whatever python3/python is on PATH.
    const QString p3 = QStandardPaths::findExecutable("python3");
    if (!p3.isEmpty()) c << p3;
    const QString p = QStandardPaths::findExecutable("python");
    if (!p.isEmpty()) c << p;
    return c;
}
} // namespace

QString AnimicaWalletBackend::findPythonInterpreter()
{
    // 1) Explicit override always wins.
    const QString override = qEnvironmentVariable("ANIMICA_WALLET_PYTHON");
    if (!override.isEmpty() && isUsablePythonExecutable(override)) {
        return QFileInfo(override).absoluteFilePath();
    }

    // 2) A bundled venv (proper native builds) is authoritative if present.
    for (const QString& candidate : bundledPythonCandidates()) {
        if (isUsablePythonExecutable(candidate)) {
            return QFileInfo(candidate).absoluteFilePath();
        }
    }

    // 3) Repo checkout venv.
    for (const QString& candidate : repoPythonCandidates()) {
        if (isUsablePythonExecutable(candidate)) {
            return QFileInfo(candidate).absoluteFilePath();
        }
    }

    // 4) No bundled/repo venv (e.g. cross-built bundle). Probe common system
    //    interpreters and pick the FIRST that can `import animica` — so a
    //    pip-installed `animica` is found automatically with no env var.
    QString firstExisting;
    for (const QString& candidate : systemPythonCandidates()) {
        if (!isUsablePythonExecutable(candidate)) {
            continue;
        }
        const QString abs = QFileInfo(candidate).absoluteFilePath();
        if (firstExisting.isEmpty()) {
            firstExisting = abs;
        }
        if (pythonHasAnimica(abs)) {
            return abs;
        }
    }
    // 5) Nothing had animica — return the first real interpreter so the caller
    //    can surface a clear "pip install animica" error rather than crashing.
    return firstExisting;
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
