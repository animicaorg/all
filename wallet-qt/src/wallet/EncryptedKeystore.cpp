#include "EncryptedKeystore.h"
#include <QFile>
#include <QJsonDocument>
#include <QJsonArray>
#include <QRandomGenerator>
#include <QMessageAuthenticationCode>
#include <QCryptographicHash>
#include <QDir>
#include <QUuid>
#include <openssl/evp.h>
#include <openssl/rand.h>
#include <cstring>

#ifdef Q_OS_WIN
#include <windows.h>
#else
#include <string.h>
#endif

#ifdef Q_OS_UNIX
#include <unistd.h>
#endif

EncryptedKeystore::EncryptedKeystore()
    : m_loaded(false)
{
}

bool EncryptedKeystore::create(const QString& path, const QByteArray& payload, const QString& password)
{
    // Generate random salt and nonce
    QByteArray salt = randomBytes(16);
    QByteArray nonce = randomBytes(12);
    
    // Derive encryption key
    int iterations = 200000;
    QByteArray key = deriveKey(password, salt, iterations);
    
    // Encrypt payload
    QByteArray ciphertext;
    QByteArray tag;
    if (!encryptAES256GCM(payload, key, nonce, ciphertext, tag)) {
        return false;
    }
    
    // Build JSON structure
    QJsonObject json;
    json["schema_version"] = 1;
    
    QJsonObject kdf;
    kdf["algorithm"] = "pbkdf2-sha3-256";
    QJsonObject kdfParams;
    kdfParams["iterations"] = iterations;
    kdfParams["salt"] = QString::fromLatin1(salt.toBase64());
    kdf["params"] = kdfParams;
    json["kdf"] = kdf;
    
    QJsonObject encryption;
    encryption["algorithm"] = "aes-256-gcm";
    encryption["nonce"] = QString::fromLatin1(nonce.toBase64());
    encryption["tag"] = QString::fromLatin1(tag.toBase64());
    json["encryption"] = encryption;
    
    json["public_accounts"] = QJsonArray();
    json["encrypted_payload"] = QString::fromLatin1(ciphertext.toBase64());
    
    QDateTime now = QDateTime::currentDateTimeUtc();
    json["created_at"] = now.toString(Qt::ISODate);
    json["updated_at"] = now.toString(Qt::ISODate);
    
    // Write atomically
    return atomicWrite(path, json);
}

bool EncryptedKeystore::load(const QString& path)
{
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        return false;
    }
    
    QByteArray data = file.readAll();
    file.close();
    
    QJsonDocument doc = QJsonDocument::fromJson(data);
    if (doc.isNull() || !doc.isObject()) {
        return false;
    }
    
    m_data = doc.object();
    m_path = path;
    m_loaded = true;
    return true;
}

bool EncryptedKeystore::unlock(const QString& password, QByteArray& outPayload)
{
    if (!m_loaded) {
        return false;
    }
    
    // Extract KDF parameters
    QJsonObject kdf = m_data["kdf"].toObject();
    QJsonObject kdfParams = kdf["params"].toObject();
    int iterations = kdfParams["iterations"].toInt();
    QByteArray salt = QByteArray::fromBase64(kdfParams["salt"].toString().toLatin1());
    
    // Derive key
    QByteArray key = deriveKey(password, salt, iterations);
    
    // Extract encryption parameters
    QJsonObject encryption = m_data["encryption"].toObject();
    QByteArray nonce = QByteArray::fromBase64(encryption["nonce"].toString().toLatin1());
    QByteArray tag = QByteArray::fromBase64(encryption["tag"].toString().toLatin1());
    QByteArray ciphertext = QByteArray::fromBase64(m_data["encrypted_payload"].toString().toLatin1());
    
    // Decrypt
    return decryptAES256GCM(ciphertext, key, nonce, tag, outPayload);
}

void EncryptedKeystore::lock()
{
    // Clear any cached decrypted data (none in this implementation)
}

bool EncryptedKeystore::save(const QByteArray& payload, const QString& password)
{
    if (!m_loaded) {
        return false;
    }
    
    // Re-use existing salt, generate new nonce
    QJsonObject kdf = m_data["kdf"].toObject();
    QJsonObject kdfParams = kdf["params"].toObject();
    int iterations = kdfParams["iterations"].toInt();
    QByteArray salt = QByteArray::fromBase64(kdfParams["salt"].toString().toLatin1());
    QByteArray nonce = randomBytes(12);
    
    // Derive key
    QByteArray key = deriveKey(password, salt, iterations);
    
    // Encrypt
    QByteArray ciphertext;
    QByteArray tag;
    if (!encryptAES256GCM(payload, key, nonce, ciphertext, tag)) {
        return false;
    }
    
    // Update JSON
    QJsonObject encryption;
    encryption["algorithm"] = "aes-256-gcm";
    encryption["nonce"] = QString::fromLatin1(nonce.toBase64());
    encryption["tag"] = QString::fromLatin1(tag.toBase64());
    m_data["encryption"] = encryption;
    m_data["encrypted_payload"] = QString::fromLatin1(ciphertext.toBase64());
    m_data["updated_at"] = QDateTime::currentDateTimeUtc().toString(Qt::ISODate);
    
    return atomicWrite(m_path, m_data);
}

bool EncryptedKeystore::changePassword(const QString& oldPassword, const QString& newPassword)
{
    // Decrypt with old password
    QByteArray payload;
    if (!unlock(oldPassword, payload)) {
        return false;
    }
    
    // Generate new salt
    QByteArray salt = randomBytes(16);
    QByteArray nonce = randomBytes(12);
    int iterations = 200000;
    
    // Derive new key
    QByteArray key = deriveKey(newPassword, salt, iterations);
    
    // Encrypt with new key
    QByteArray ciphertext;
    QByteArray tag;
    if (!encryptAES256GCM(payload, key, nonce, ciphertext, tag)) {
        return false;
    }
    
    // Update JSON
    QJsonObject kdfParams;
    kdfParams["iterations"] = iterations;
    kdfParams["salt"] = QString::fromLatin1(salt.toBase64());
    QJsonObject kdf;
    kdf["algorithm"] = "pbkdf2-sha3-256";
    kdf["params"] = kdfParams;
    m_data["kdf"] = kdf;
    
    QJsonObject encryption;
    encryption["algorithm"] = "aes-256-gcm";
    encryption["nonce"] = QString::fromLatin1(nonce.toBase64());
    encryption["tag"] = QString::fromLatin1(tag.toBase64());
    m_data["encryption"] = encryption;
    m_data["encrypted_payload"] = QString::fromLatin1(ciphertext.toBase64());
    m_data["updated_at"] = QDateTime::currentDateTimeUtc().toString(Qt::ISODate);
    
    return atomicWrite(m_path, m_data);
}

KeystoreInfo EncryptedKeystore::readInfo() const
{
    KeystoreInfo info;
    info.schemaVersion = m_data["schema_version"].toInt();
    info.kdfAlgorithm = m_data["kdf"].toObject()["algorithm"].toString();
    info.kdfParams = m_data["kdf"].toObject()["params"].toObject();
    info.encryptionAlgorithm = m_data["encryption"].toObject()["algorithm"].toString();
    info.accountCount = m_data["public_accounts"].toArray().size();
    info.createdAt = QDateTime::fromString(m_data["created_at"].toString(), Qt::ISODate);
    info.updatedAt = QDateTime::fromString(m_data["updated_at"].toString(), Qt::ISODate);
    return info;
}

QByteArray EncryptedKeystore::deriveKey(const QString& password, const QByteArray& salt, int iterations)
{
    QByteArray key(32, 0);
    QByteArray passwordBytes = password.toUtf8();
    
    // PBKDF2-HMAC-SHA3-256
    if (PKCS5_PBKDF2_HMAC(passwordBytes.constData(), passwordBytes.size(),
                          reinterpret_cast<const unsigned char*>(salt.constData()), salt.size(),
                          iterations,
                          EVP_sha3_256(),
                          key.size(), reinterpret_cast<unsigned char*>(key.data())) != 1) {
        // Clear password from memory before returning
#ifdef Q_OS_WIN
        SecureZeroMemory(passwordBytes.data(), passwordBytes.size());
#elif defined(Q_OS_MACOS)
        memset_s(passwordBytes.data(), passwordBytes.size(), 0, passwordBytes.size());
#else
        explicit_bzero(passwordBytes.data(), passwordBytes.size());
#endif
        return QByteArray();
    }
    
    // Clear password from memory
#ifdef Q_OS_WIN
    SecureZeroMemory(passwordBytes.data(), passwordBytes.size());
#elif defined(Q_OS_MACOS)
    memset_s(passwordBytes.data(), passwordBytes.size(), 0, passwordBytes.size());
#else
    explicit_bzero(passwordBytes.data(), passwordBytes.size());
#endif
    
    return key;
}

bool EncryptedKeystore::encryptAES256GCM(const QByteArray& plaintext, const QByteArray& key,
                                          const QByteArray& nonce, QByteArray& outCiphertext,
                                          QByteArray& outTag)
{
    EVP_CIPHER_CTX* ctx = EVP_CIPHER_CTX_new();
    if (!ctx) {
        return false;
    }
    
    bool success = false;
    do {
        // Initialize encryption
        if (EVP_EncryptInit_ex(ctx, EVP_aes_256_gcm(), nullptr,
                               reinterpret_cast<const unsigned char*>(key.constData()),
                               reinterpret_cast<const unsigned char*>(nonce.constData())) != 1) {
            break;
        }
        
        // Allocate output buffer
        outCiphertext.resize(plaintext.size() + EVP_CIPHER_CTX_block_size(ctx));
        int len = 0;
        
        // Encrypt
        if (EVP_EncryptUpdate(ctx, reinterpret_cast<unsigned char*>(outCiphertext.data()), &len,
                              reinterpret_cast<const unsigned char*>(plaintext.constData()),
                              plaintext.size()) != 1) {
            break;
        }
        int ciphertext_len = len;
        
        // Finalize
        if (EVP_EncryptFinal_ex(ctx, reinterpret_cast<unsigned char*>(outCiphertext.data()) + len, &len) != 1) {
            break;
        }
        ciphertext_len += len;
        outCiphertext.resize(ciphertext_len);
        
        // Get tag
        outTag.resize(16);
        if (EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_GET_TAG, 16,
                                reinterpret_cast<unsigned char*>(outTag.data())) != 1) {
            break;
        }
        
        success = true;
    } while (false);
    
    // Clear sensitive data on failure
    if (!success) {
        outCiphertext.fill(0);
        outTag.fill(0);
    }
    
    EVP_CIPHER_CTX_free(ctx);
    return success;
}

bool EncryptedKeystore::decryptAES256GCM(const QByteArray& ciphertext, const QByteArray& key,
                                          const QByteArray& nonce, const QByteArray& tag,
                                          QByteArray& outPlaintext)
{
    EVP_CIPHER_CTX* ctx = EVP_CIPHER_CTX_new();
    if (!ctx) {
        return false;
    }
    
    bool success = false;
    do {
        // Initialize decryption
        if (EVP_DecryptInit_ex(ctx, EVP_aes_256_gcm(), nullptr,
                               reinterpret_cast<const unsigned char*>(key.constData()),
                               reinterpret_cast<const unsigned char*>(nonce.constData())) != 1) {
            break;
        }
        
        // Allocate output buffer
        outPlaintext.resize(ciphertext.size() + EVP_CIPHER_CTX_block_size(ctx));
        int len = 0;
        
        // Decrypt
        if (EVP_DecryptUpdate(ctx, reinterpret_cast<unsigned char*>(outPlaintext.data()), &len,
                              reinterpret_cast<const unsigned char*>(ciphertext.constData()),
                              ciphertext.size()) != 1) {
            break;
        }
        int plaintext_len = len;
        
        // Set expected tag
        QByteArray tagCopy = tag;
        if (EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_TAG, tagCopy.size(),
                                reinterpret_cast<unsigned char*>(tagCopy.data())) != 1) {
            break;
        }
        
        // Finalize and verify tag
        if (EVP_DecryptFinal_ex(ctx, reinterpret_cast<unsigned char*>(outPlaintext.data()) + len, &len) != 1) {
            break;
        }
        plaintext_len += len;
        outPlaintext.resize(plaintext_len);
        
        success = true;
    } while (false);
    
    EVP_CIPHER_CTX_free(ctx);
    return success;
}

QByteArray EncryptedKeystore::randomBytes(int size)
{
    QByteArray bytes(size, 0);
    if (RAND_bytes(reinterpret_cast<unsigned char*>(bytes.data()), size) != 1) {
        return QByteArray();
    }
    return bytes;
}

bool EncryptedKeystore::atomicWrite(const QString& path, const QJsonObject& json)
{
    QString tmpPath = path + ".tmp." + QUuid::createUuid().toString(QUuid::WithoutBraces);
    QString backupPath = path + ".backup";
    
    QFile tmp(tmpPath);
    if (!tmp.open(QIODevice::WriteOnly)) {
        return false;
    }
    
    QJsonDocument doc(json);
    QByteArray data = doc.toJson(QJsonDocument::Indented);
    
    if (tmp.write(data) != data.size()) {
        tmp.close();
        QFile::remove(tmpPath);
        return false;
    }
    
    tmp.flush();
#ifdef Q_OS_UNIX
    fsync(tmp.handle());
#endif
    tmp.close();
    
    setRestrictivePermissions(tmpPath);
    
    // Safer atomic rename with backup
    if (QFile::exists(path)) {
        // Create backup of existing file
        QFile::remove(backupPath);
        if (!QFile::rename(path, backupPath)) {
            QFile::remove(tmpPath);
            return false;
        }
    }
    
    // Rename temp to target
    if (!QFile::rename(tmpPath, path)) {
        // Restore backup on failure
        if (QFile::exists(backupPath)) {
            QFile::rename(backupPath, path);
        }
        QFile::remove(tmpPath);
        return false;
    }
    
    // Remove backup on success
    QFile::remove(backupPath);
    
    return true;
}

void EncryptedKeystore::setRestrictivePermissions(const QString& path)
{
#ifdef Q_OS_UNIX
    QFile::setPermissions(path, QFileDevice::ReadOwner | QFileDevice::WriteOwner);
#endif
}
