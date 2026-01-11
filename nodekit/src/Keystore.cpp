#include "AnimicaNodeKit/Keystore.h"

#include <QDateTime>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QRandomGenerator>

#include <openssl/evp.h>
#include <openssl/rand.h>
#include <openssl/crypto.h>

namespace animica::nodekit {

namespace {

constexpr int kSaltSize = 16;
constexpr int kNonceSize = 12;
constexpr int kKeySize = 32;
constexpr int kTagSize = 16;

QByteArray randomBytes(int size) {
    QByteArray bytes(size, Qt::Uninitialized);
    RAND_bytes(reinterpret_cast<unsigned char *>(bytes.data()), size);
    return bytes;
}

bool deriveKey(const QString &passphrase, const QByteArray &salt, QByteArray *keyOut) {
    keyOut->resize(kKeySize);
    const QByteArray passBytes = passphrase.toUtf8();
    const int result = EVP_PBE_scrypt(passBytes.constData(), passBytes.size(),
                                      reinterpret_cast<const unsigned char *>(salt.constData()),
                                      salt.size(), 1 << 15, 8, 1, 0,
                                      reinterpret_cast<unsigned char *>(keyOut->data()), kKeySize);
    return result == 1;
}

QByteArray encodeBase64(const QByteArray &data) {
    return data.toBase64(QByteArray::Base64UrlEncoding | QByteArray::OmitTrailingEquals);
}

QByteArray decodeBase64(const QByteArray &data) {
    return QByteArray::fromBase64(data, QByteArray::Base64UrlEncoding);
}

} // namespace

Keystore::Keystore(QObject *parent) : QObject(parent) {}

void Keystore::setStoragePath(const QString &path) {
    storagePath_ = path;
}

bool Keystore::unlock(const QString &passphrase) {
    passphrase_ = passphrase;
    if (!loadEncrypted()) {
        plaintext_ = buildNewKeystore();
        if (!saveEncrypted()) {
            passphrase_.clear();
            return false;
        }
    }
    unlocked_ = true;
    return true;
}

void Keystore::lock() {
    unlocked_ = false;
    passphrase_.clear();
    plaintext_ = QJsonObject{};
}

bool Keystore::isUnlocked() const {
    return unlocked_;
}

QVector<WalletEntry> Keystore::wallets() const {
    QVector<WalletEntry> entries;
    const QJsonArray wallets = plaintext_.value("wallets").toArray();
    for (const QJsonValue &value : wallets) {
        const QJsonObject wallet = value.toObject();
        entries.push_back({wallet.value("label").toString(),
                           wallet.value("address").toString(),
                           wallet.value("alg_id").toString(),
                           wallet.value("public_key").toString(),
                           wallet.value("created_at").toString()});
    }
    return entries;
}

bool Keystore::createWallet(const QString &label, const QString &algId) {
    if (!unlocked_) {
        return false;
    }

    QByteArray secret = randomBytes(32);
    QByteArray pub = randomBytes(32);
    QByteArray addr = randomBytes(20);

    QJsonObject wallet{{"label", label},
                       {"address", QString::fromUtf8(addr.toHex())},
                       {"alg_id", algId},
                       {"public_key", QString::fromUtf8(pub.toHex())},
                       {"created_at", QDateTime::currentDateTimeUtc().toString(Qt::ISODate)},
                       {"secret", QString::fromUtf8(secret.toHex())}};

    QJsonArray wallets = plaintext_.value("wallets").toArray();
    wallets.append(wallet);
    plaintext_["wallets"] = wallets;
    return saveEncrypted();
}

bool Keystore::importWallet(const QString &label, const QString &secret) {
    if (!unlocked_) {
        return false;
    }

    QJsonObject wallet{{"label", label},
                       {"address", QString::fromUtf8(QRandomGenerator::global()->generate64())},
                       {"alg_id", QStringLiteral("pq_default")},
                       {"public_key", QStringLiteral("imported")},
                       {"created_at", QDateTime::currentDateTimeUtc().toString(Qt::ISODate)},
                       {"secret", secret}};

    QJsonArray wallets = plaintext_.value("wallets").toArray();
    wallets.append(wallet);
    plaintext_["wallets"] = wallets;
    return saveEncrypted();
}

QJsonObject Keystore::signTx(const QJsonObject &tx, QString *error) {
    if (!unlocked_) {
        if (error) {
            *error = QStringLiteral("keystore locked");
        }
        return {};
    }

    QJsonObject signedTx = tx;
    signedTx["signature"] = QStringLiteral("stub-signature");
    return signedTx;
}

bool Keystore::loadEncrypted() {
    QFile file(storagePath_);
    if (!file.exists()) {
        return false;
    }
    if (!file.open(QIODevice::ReadOnly)) {
        return false;
    }

    const QJsonDocument doc = QJsonDocument::fromJson(file.readAll());
    file.close();
    if (!doc.isObject()) {
        return false;
    }

    const QJsonObject root = doc.object();
    const QByteArray salt = decodeBase64(root.value("salt").toString().toUtf8());
    const QByteArray nonce = decodeBase64(root.value("nonce").toString().toUtf8());
    const QByteArray tag = decodeBase64(root.value("tag").toString().toUtf8());
    const QByteArray cipherText = decodeBase64(root.value("ciphertext").toString().toUtf8());

    QByteArray key;
    if (!deriveKey(passphrase_, salt, &key)) {
        return false;
    }

    QByteArray plain(cipherText.size(), Qt::Uninitialized);
    int len = 0;
    int plainLen = 0;
    EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
    EVP_DecryptInit_ex(ctx, EVP_aes_256_gcm(), nullptr, nullptr, nullptr);
    EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_IVLEN, nonce.size(), nullptr);
    EVP_DecryptInit_ex(ctx, nullptr, nullptr,
                       reinterpret_cast<const unsigned char *>(key.constData()),
                       reinterpret_cast<const unsigned char *>(nonce.constData()));
    EVP_DecryptUpdate(ctx, reinterpret_cast<unsigned char *>(plain.data()), &len,
                      reinterpret_cast<const unsigned char *>(cipherText.constData()),
                      cipherText.size());
    plainLen = len;
    EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_TAG, tag.size(), const_cast<char *>(tag.constData()));
    const int ok = EVP_DecryptFinal_ex(ctx, reinterpret_cast<unsigned char *>(plain.data()) + len, &len);
    EVP_CIPHER_CTX_free(ctx);

    if (ok <= 0) {
        return false;
    }
    plainLen += len;
    plain.resize(plainLen);

    const QJsonDocument plaintextDoc = QJsonDocument::fromJson(plain);
    if (!plaintextDoc.isObject()) {
        return false;
    }

    plaintext_ = plaintextDoc.object();
    return true;
}

bool Keystore::saveEncrypted() {
    if (storagePath_.isEmpty()) {
        return false;
    }

    const QJsonDocument doc(plaintext_);
    const QByteArray plain = doc.toJson(QJsonDocument::Compact);

    const QByteArray salt = randomBytes(kSaltSize);
    const QByteArray nonce = randomBytes(kNonceSize);

    QByteArray key;
    if (!deriveKey(passphrase_, salt, &key)) {
        return false;
    }

    QByteArray cipherText(plain.size(), Qt::Uninitialized);
    QByteArray tag(kTagSize, Qt::Uninitialized);
    int len = 0;
    int cipherLen = 0;
    EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
    EVP_EncryptInit_ex(ctx, EVP_aes_256_gcm(), nullptr, nullptr, nullptr);
    EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_IVLEN, nonce.size(), nullptr);
    EVP_EncryptInit_ex(ctx, nullptr, nullptr,
                       reinterpret_cast<const unsigned char *>(key.constData()),
                       reinterpret_cast<const unsigned char *>(nonce.constData()));
    EVP_EncryptUpdate(ctx, reinterpret_cast<unsigned char *>(cipherText.data()), &len,
                      reinterpret_cast<const unsigned char *>(plain.constData()), plain.size());
    cipherLen = len;
    EVP_EncryptFinal_ex(ctx, reinterpret_cast<unsigned char *>(cipherText.data()) + len, &len);
    cipherLen += len;
    EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_GET_TAG, tag.size(), tag.data());
    EVP_CIPHER_CTX_free(ctx);
    cipherText.resize(cipherLen);

    QJsonObject root{{"kdf", "scrypt"},
                     {"salt", QString::fromUtf8(encodeBase64(salt))},
                     {"nonce", QString::fromUtf8(encodeBase64(nonce))},
                     {"tag", QString::fromUtf8(encodeBase64(tag))},
                     {"ciphertext", QString::fromUtf8(encodeBase64(cipherText))}};

    QFile file(storagePath_);
    if (!file.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
        return false;
    }
    file.write(QJsonDocument(root).toJson(QJsonDocument::Compact));
    file.close();

    OPENSSL_cleanse(key.data(), key.size());
    return true;
}

QJsonObject Keystore::buildNewKeystore() const {
    return QJsonObject{{"wallets", QJsonArray{}}, {"created_at", QDateTime::currentDateTimeUtc().toString(Qt::ISODate)}};
}

} // namespace animica::nodekit
