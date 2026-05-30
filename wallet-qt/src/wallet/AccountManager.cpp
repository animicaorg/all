#include "AccountManager.h"
#include <QProcess>
#include <QUuid>
#include <QJsonArray>
#include <QJsonDocument>
#include <QRegularExpression>
#include <QDebug>

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
    
    QProcess process;
    process.start("python", QStringList() << "-c" << pythonCode);
    
    if (!process.waitForStarted()) {
        qWarning() << "Failed to start Python keygen process";
        return false;
    }
    
    if (!process.waitForFinished(15000)) {
        qWarning() << "Python keygen process timeout";
        process.kill();
        return false;
    }
    
    if (process.exitCode() != 0) {
        qWarning() << "Python keygen failed:" << process.readAllStandardError();
        return false;
    }
    
    // Parse JSON output: {"public_key": "hex", "secret_key": "hex"}
    QByteArray output = process.readAllStandardOutput();
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
    
    QProcess process;
    process.start("python", QStringList() << "-c" << pythonCode);
    
    if (!process.waitForStarted()) {
        qWarning() << "Failed to start Python address process";
        return QString();
    }
    
    if (!process.waitForFinished(10000)) {
        qWarning() << "Python address process timeout";
        process.kill();
        return QString();
    }
    
    if (process.exitCode() != 0) {
        qWarning() << "Python address failed:" << process.readAllStandardError();
        return QString();
    }
    
    QString address = QString::fromUtf8(process.readAllStandardOutput()).trimmed();
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
