#include "AccountManager.h"
#include "AnimicaWalletBackend.h"
#include <QProcess>
#include <QUuid>
#include <QJsonArray>
#include <QJsonDocument>
#include <QRegularExpression>
#include <QDir>
#include <QProcessEnvironment>
#include <QDebug>

namespace {
// Run `python -c <code>` against the SAME interpreter the rest of the wallet
// uses (bundled venv first, then repo/system) instead of a bare "python" off
// PATH. The bare form only worked on machines that happened to have Python
// installed — on clean Windows boxes it failed to start and surfaced as a
// "Python was not found" error. Mirrors AnimicaWalletBackend::call()'s env.
bool runPythonInline(const QString& code, int timeoutMs, QByteArray& outStdout)
{
    const QString python = AnimicaWalletBackend::findPythonInterpreter();
    if (python.isEmpty()) {
        qWarning() << "No Python interpreter found for the Animica wallet backend";
        return false;
    }

    QProcess process;
    QProcessEnvironment env = QProcessEnvironment::systemEnvironment();
    const QString repoRoot = AnimicaWalletBackend::findRepoRoot();
    if (!repoRoot.isEmpty()) {
        const QString sep =
#ifdef Q_OS_WIN
            ";";
#else
            ":";
#endif
        QStringList pyPath = env.value("PYTHONPATH").split(sep, Qt::SkipEmptyParts);
        pyPath.prepend(QDir(repoRoot).filePath("sdk/python"));
        pyPath.prepend(QDir(repoRoot).filePath("python"));
        env.insert("PYTHONPATH", pyPath.join(sep));
    }
    process.setProcessEnvironment(env);

    process.start(python, QStringList() << "-c" << code);
    if (!process.waitForStarted(5000)) {
        qWarning() << "Failed to start Python process:" << python << process.errorString();
        return false;
    }
    if (!process.waitForFinished(timeoutMs)) {
        qWarning() << "Python process timeout";
        process.kill();
        process.waitForFinished(500);
        return false;
    }
    if (process.exitStatus() != QProcess::NormalExit || process.exitCode() != 0) {
        qWarning() << "Python process failed:" << process.readAllStandardError();
        return false;
    }
    outStdout = process.readAllStandardOutput();
    return true;
}
} // namespace

AccountManager::AccountManager(QObject* parent)
    : QObject(parent)
{
}

WalletAccount AccountManager::createAccount(const QString& label, int algId)
{
    WalletAccount account;
    account.accountId = QUuid::createUuid().toString(QUuid::WithoutBraces);
    account.label = label;
    account.algId = algId;
    account.algName = algIdToName(algId);
    account.createdAt = QDateTime::currentDateTimeUtc();
    account.lastUsedAt = account.createdAt;
    account.isDefault = m_accounts.isEmpty();
    
    // Generate keypair
    if (!generateKeypair(algId, account.publicKey, account.secretKey)) {
        qWarning() << "Failed to generate keypair for algorithm" << algId;
        return WalletAccount();
    }
    
    // Derive address
    account.address = deriveAddress(account.publicKey, algId);
    if (account.address.isEmpty()) {
        qWarning() << "Failed to derive address";
        return WalletAccount();
    }
    
    m_accounts.append(account);
    emit accountAdded(account);
    
    return account;
}

WalletAccount AccountManager::importAccount(const QJsonObject& json)
{
    WalletAccount account = WalletAccount::fromSecretJson(json);
    
    if (account.accountId.isEmpty()) {
        account.accountId = QUuid::createUuid().toString(QUuid::WithoutBraces);
    }
    if (account.createdAt.isNull()) {
        account.createdAt = QDateTime::currentDateTimeUtc();
    }
    if (account.lastUsedAt.isNull()) {
        account.lastUsedAt = account.createdAt;
    }
    
    // Derive address if not present
    if (account.address.isEmpty() && !account.publicKey.isEmpty()) {
        account.address = deriveAddress(account.publicKey, account.algId);
    }
    
    account.isDefault = m_accounts.isEmpty();
    m_accounts.append(account);
    emit accountAdded(account);
    
    return account;
}

bool AccountManager::renameAccount(const QString& accountId, const QString& newLabel)
{
    for (auto& account : m_accounts) {
        if (account.accountId == accountId) {
            account.label = newLabel;
            emit accountUpdated(account);
            return true;
        }
    }
    return false;
}

WalletAccount AccountManager::setDefault(const QString& accountId)
{
    WalletAccount result;
    for (auto& account : m_accounts) {
        if (account.accountId == accountId) {
            account.isDefault = true;
            result = account;
            emit accountUpdated(account);
        } else if (account.isDefault) {
            account.isDefault = false;
            emit accountUpdated(account);
        }
    }
    return result;
}

void AccountManager::updateLastUsed(const QString& accountId)
{
    for (auto& account : m_accounts) {
        if (account.accountId == accountId) {
            account.lastUsedAt = QDateTime::currentDateTimeUtc();
            emit accountUpdated(account);
            break;
        }
    }
}

WalletAccount AccountManager::getAccount(const QString& accountId) const
{
    for (const auto& account : m_accounts) {
        if (account.accountId == accountId) {
            return account;
        }
    }
    return WalletAccount();
}

QList<WalletAccount> AccountManager::listAccounts() const
{
    return m_accounts;
}

void AccountManager::loadAccounts(const QByteArray& payload, const QJsonArray& publicAccounts)
{
    clearAccounts();
    
    QJsonDocument doc = QJsonDocument::fromJson(payload);
    if (!doc.isObject()) {
        return;
    }
    
    QJsonObject obj = doc.object();
    QJsonArray secretAccounts = obj["accounts"].toArray();
    
    // Build map of account_id -> public metadata
    QMap<QString, QJsonObject> publicMap;
    for (const QJsonValue& val : publicAccounts) {
        QJsonObject pub = val.toObject();
        QString id = pub["account_id"].toString();
        publicMap[id] = pub;
    }
    
    // Load accounts with secret keys
    for (const QJsonValue& val : secretAccounts) {
        QJsonObject secret = val.toObject();
        QString accountId = secret["account_id"].toString();
        
        WalletAccount account = WalletAccount::fromSecretJson(secret);
        
        // Merge public metadata
        if (publicMap.contains(accountId)) {
            QJsonObject pub = publicMap[accountId];
            account.accountId = pub["account_id"].toString();
            account.label = pub["label"].toString();
            account.address = pub["address"].toString();
            account.createdAt = QDateTime::fromString(pub["created_at"].toString(), Qt::ISODate);
            account.lastUsedAt = QDateTime::fromString(pub["last_used_at"].toString(), Qt::ISODate);
        }
        
        m_accounts.append(account);
    }
}

void AccountManager::saveAccounts(QByteArray& outPayload, QJsonArray& outPublicAccounts)
{
    QJsonArray secretAccounts;
    outPublicAccounts = QJsonArray();
    
    for (const auto& account : m_accounts) {
        secretAccounts.append(account.toSecretJson());
        outPublicAccounts.append(account.toPublicJson());
    }
    
    QJsonObject payload;
    payload["accounts"] = secretAccounts;
    payload["master_seed"] = QJsonValue::Null;
    payload["address_book_notes"] = QJsonObject();
    
    QJsonDocument doc(payload);
    outPayload = doc.toJson(QJsonDocument::Compact);
}

void AccountManager::clearAccounts()
{
    for (auto& account : m_accounts) {
        account.clearSecrets();
    }
    m_accounts.clear();
}

bool AccountManager::generateKeypair(int algId, QByteArray& outPublicKey, QByteArray& outSecretKey)
{
    // ml_dsa_65 (0x1003, FIPS 204) is the only signature scheme the network
    // accepts. dilithium3 (0x1001) and sphincs_shake_128s (0x1002) are
    // deprecated, forgeable stubs whose verify() ignores the secret key — a
    // new wallet on either scheme would be unspendable and forgeable. Never
    // mint a fresh keypair for anything but ml_dsa_65. Existing legacy wallets
    // still load and label (algIdToName keeps the old names); they just can't
    // be created here.
    if (algId != 0x1003) {
        qWarning() << "Refusing to generate a keypair for alg id" << Qt::hex << algId
                   << "— only ml_dsa_65 (0x1003) can be created.";
        return false;
    }

    QString algName = algIdToName(algId);
    if (algName.isEmpty()) {
        return false;
    }

    // Validate algName to prevent injection (should only be from our whitelist)
    if (!algName.contains(QRegularExpression("^[a-z0-9_]+$"))) {
        qWarning() << "Invalid algorithm name:" << algName;
        return false;
    }
    
    // Call Python: python -c "from pq.py.keygen import keygen_sig; import json; kp = keygen_sig('dilithium3'); print(json.dumps({'public_key': kp.public_key.hex(), 'secret_key': kp.secret_key.hex()}))"
    QString pythonCode = QString(
        "from pq.py.keygen import keygen_sig; "
        "import json; "
        "kp = keygen_sig('%1'); "
        "print(json.dumps({'public_key': kp.public_key.hex(), 'secret_key': kp.secret_key.hex()}))"
    ).arg(algName);
    
    QByteArray output;
    if (!runPythonInline(pythonCode, 15000, output)) {
        qWarning() << "Python keygen failed";
        return false;
    }

    // Parse JSON output: {"public_key": "hex", "secret_key": "hex"}
    QJsonDocument doc = QJsonDocument::fromJson(output);
    if (!doc.isObject()) {
        qWarning() << "Invalid keygen output:" << output;
        return false;
    }
    
    QJsonObject obj = doc.object();
    outPublicKey = QByteArray::fromHex(obj["public_key"].toString().toLatin1());
    outSecretKey = QByteArray::fromHex(obj["secret_key"].toString().toLatin1());
    
    return !outPublicKey.isEmpty() && !outSecretKey.isEmpty();
}

QString AccountManager::deriveAddress(const QByteArray& publicKey, int algId)
{
    // Validate hex encoding
    QString pubkeyHex = QString::fromLatin1(publicKey.toHex());
    if (!pubkeyHex.contains(QRegularExpression("^[0-9a-f]*$"))) {
        qWarning() << "Invalid public key hex encoding";
        return QString();
    }
    
    // Call Python: python -c "from pq.py.address import address_from_pubkey; print(address_from_pubkey(bytes.fromhex('...'), 0x1001))"
    QString pythonCode = QString(
        "from pq.py.address import address_from_pubkey; "
        "print(address_from_pubkey(bytes.fromhex('%1'), %2))"
    ).arg(pubkeyHex).arg(algId);
    
    QByteArray output;
    if (!runPythonInline(pythonCode, 10000, output)) {
        qWarning() << "Python address derivation failed";
        return QString();
    }

    QString address = QString::fromUtf8(output).trimmed();
    return address;
}

QString AccountManager::algIdToName(int algId) const
{
    switch (algId) {
        case 0x1001:
            return "dilithium3";
        case 0x1002:
            return "sphincs-shake-128s";
        case 0x1003:
            return "ml_dsa_65";
        default:
            return QString();
    }
}
