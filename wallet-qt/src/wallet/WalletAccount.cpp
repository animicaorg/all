#include "WalletAccount.h"
#include <QJsonArray>
#include <QUuid>
#include <cstring>

#ifdef Q_OS_WIN
#include <windows.h>
#else
#include <string.h>
#endif

WalletAccount::WalletAccount()
    : algId(0x1003)
    , algName("ml_dsa_65")
    , isDefault(false)
{
}

QJsonObject WalletAccount::toPublicJson() const
{
    QJsonObject json;
    json["account_id"] = accountId;
    json["label"] = label;
    json["address"] = address;
    json["alg_id"] = algId;
    json["created_at"] = createdAt.toString(Qt::ISODate);
    json["last_used_at"] = lastUsedAt.toString(Qt::ISODate);
    return json;
}

QJsonObject WalletAccount::toSecretJson() const
{
    QJsonObject json;
    json["account_id"] = accountId;
    json["alg_id"] = algId;
    json["alg_name"] = algName;
    json["public_key"] = QString::fromLatin1(publicKey.toHex());
    json["secret_key"] = QString::fromLatin1(secretKey.toHex());
    json["seed"] = QJsonValue::Null;  // Not used for Dilithium3
    return json;
}

WalletAccount WalletAccount::fromPublicJson(const QJsonObject& json)
{
    WalletAccount account;
    account.accountId = json["account_id"].toString();
    account.label = json["label"].toString();
    account.address = json["address"].toString();
    account.algId = json["alg_id"].toInt();
    account.createdAt = QDateTime::fromString(json["created_at"].toString(), Qt::ISODate);
    account.lastUsedAt = QDateTime::fromString(json["last_used_at"].toString(), Qt::ISODate);
    return account;
}

WalletAccount WalletAccount::fromSecretJson(const QJsonObject& json)
{
    WalletAccount account;
    account.accountId = json["account_id"].toString();
    account.algId = json["alg_id"].toInt();
    account.algName = json["alg_name"].toString();
    account.publicKey = QByteArray::fromHex(json["public_key"].toString().toLatin1());
    account.secretKey = QByteArray::fromHex(json["secret_key"].toString().toLatin1());
    return account;
}

void WalletAccount::clearSecrets()
{
    if (!secretKey.isEmpty()) {
#ifdef Q_OS_WIN
        SecureZeroMemory(secretKey.data(), secretKey.size());
#elif defined(Q_OS_MACOS)
        memset_s(secretKey.data(), secretKey.size(), 0, secretKey.size());
#else
        explicit_bzero(secretKey.data(), secretKey.size());
#endif
        secretKey.clear();
    }
}

bool WalletAccount::hasSecretKey() const
{
    return !secretKey.isEmpty();
}
