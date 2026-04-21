#ifndef WALLETSECURITYSETTINGS_H
#define WALLETSECURITYSETTINGS_H

#include <QByteArray>
#include <QRandomGenerator>
#include <QSettings>
#include <QString>

#include <openssl/crypto.h>
#include <openssl/evp.h>

namespace WalletSecuritySettings {

constexpr const char* kTransferPasswordSaltKey = "WalletQt/security/transferPasswordSalt";
constexpr const char* kTransferPasswordHashKey = "WalletQt/security/transferPasswordHash";
constexpr const char* kRequireTransferPasswordKey = "WalletQt/security/requireTransferPassword";
constexpr const char* kWalletEncryptionEnabledKey = "WalletQt/security/walletEncryptionEnabled";
constexpr int kMinPasswordLength = 8;
constexpr int kPbkdf2Iterations = 200000;
constexpr int kDerivedKeyBytes = 32;

inline QByteArray randomSalt(int size = 16)
{
    QByteArray salt(size, '\0');
    for (int i = 0; i < size; ++i) {
        salt[i] = static_cast<char>(QRandomGenerator::global()->bounded(256));
    }
    return salt;
}

inline bool constantTimeEquals(const QByteArray& left, const QByteArray& right)
{
    if (left.size() != right.size()) {
        return false;
    }

    unsigned char diff = 0;
    for (int i = 0; i < left.size(); ++i) {
        diff |= static_cast<unsigned char>(left.at(i) ^ right.at(i));
    }
    return diff == 0;
}

inline QByteArray deriveVerifier(const QString& password, const QByteArray& salt)
{
    if (password.isEmpty() || salt.isEmpty()) {
        return QByteArray();
    }

    QByteArray passwordBytes = password.toUtf8();
    QByteArray derived(kDerivedKeyBytes, '\0');

    const int ok = PKCS5_PBKDF2_HMAC(
        passwordBytes.constData(),
        passwordBytes.size(),
        reinterpret_cast<const unsigned char*>(salt.constData()),
        salt.size(),
        kPbkdf2Iterations,
        EVP_sha256(),
        derived.size(),
        reinterpret_cast<unsigned char*>(derived.data())
    );

    if (!passwordBytes.isEmpty()) {
        OPENSSL_cleanse(passwordBytes.data(), static_cast<size_t>(passwordBytes.size()));
    }

    if (ok != 1) {
        if (!derived.isEmpty()) {
            OPENSSL_cleanse(derived.data(), static_cast<size_t>(derived.size()));
        }
        return QByteArray();
    }

    return derived;
}

inline bool hasTransferPassword()
{
    const QSettings settings;
    const QByteArray salt = settings.value(kTransferPasswordSaltKey).toString().toLatin1();
    const QByteArray hash = settings.value(kTransferPasswordHashKey).toString().toLatin1();
    return !salt.isEmpty() && !hash.isEmpty();
}

inline bool setTransferPassword(const QString& password)
{
    if (password.size() < kMinPasswordLength) {
        return false;
    }

    const QByteArray salt = randomSalt();
    QByteArray verifier = deriveVerifier(password, salt);
    if (verifier.isEmpty()) {
        return false;
    }

    QSettings settings;
    settings.setValue(kTransferPasswordSaltKey, QString::fromLatin1(salt.toBase64()));
    settings.setValue(kTransferPasswordHashKey, QString::fromLatin1(verifier.toHex()));
    settings.sync();

    if (!verifier.isEmpty()) {
        OPENSSL_cleanse(verifier.data(), static_cast<size_t>(verifier.size()));
    }

    return true;
}

inline bool verifyTransferPassword(const QString& password)
{
    if (password.isEmpty()) {
        return false;
    }

    const QSettings settings;
    const QByteArray salt = QByteArray::fromBase64(settings.value(kTransferPasswordSaltKey).toString().toLatin1());
    const QByteArray expected = QByteArray::fromHex(settings.value(kTransferPasswordHashKey).toString().toLatin1());
    if (salt.isEmpty() || expected.isEmpty()) {
        return false;
    }

    QByteArray actual = deriveVerifier(password, salt);
    if (actual.isEmpty()) {
        return false;
    }

    const bool matches = constantTimeEquals(actual, expected);
    OPENSSL_cleanse(actual.data(), static_cast<size_t>(actual.size()));
    return matches;
}

inline bool clearTransferPassword(const QString& currentPassword)
{
    if (!verifyTransferPassword(currentPassword)) {
        return false;
    }

    QSettings settings;
    settings.remove(kTransferPasswordSaltKey);
    settings.remove(kTransferPasswordHashKey);
    settings.setValue(kRequireTransferPasswordKey, false);
    settings.setValue(kWalletEncryptionEnabledKey, false);
    settings.sync();
    return true;
}

inline bool requireTransferPasswordForSend()
{
    const QSettings settings;
    return hasTransferPassword() && settings.value(kRequireTransferPasswordKey, false).toBool();
}

inline void setRequireTransferPasswordForSend(bool enabled)
{
    QSettings settings;
    settings.setValue(kRequireTransferPasswordKey, enabled);
    settings.sync();
}

inline bool walletEncryptionEnabled()
{
    const QSettings settings;
    return hasTransferPassword() && settings.value(kWalletEncryptionEnabledKey, false).toBool();
}

inline void setWalletEncryptionEnabled(bool enabled)
{
    QSettings settings;
    settings.setValue(kWalletEncryptionEnabledKey, enabled);
    settings.sync();
}

} // namespace WalletSecuritySettings

#endif // WALLETSECURITYSETTINGS_H
