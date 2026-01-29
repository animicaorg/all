#ifndef ENCRYPTEDKEYSTORE_H
#define ENCRYPTEDKEYSTORE_H

#include <QString>
#include <QByteArray>
#include <QJsonObject>
#include <QDateTime>

/**
 * @brief Keystore metadata (no decryption needed).
 */
struct KeystoreInfo {
    int schemaVersion;
    QString kdfAlgorithm;
    QJsonObject kdfParams;
    QString encryptionAlgorithm;
    int accountCount;
    QDateTime createdAt;
    QDateTime updatedAt;
};

/**
 * @brief Encrypted keystore using PBKDF2-SHA3-256 + AES-256-GCM.
 * 
 * File format:
 * {
 *   "schema_version": 1,
 *   "kdf": {
 *     "algorithm": "pbkdf2-sha3-256",
 *     "params": {
 *       "iterations": 200000,
 *       "salt": "<base64>"
 *     }
 *   },
 *   "encryption": {
 *     "algorithm": "aes-256-gcm",
 *     "nonce": "<base64>",
 *     "tag": "<base64>"
 *   },
 *   "public_accounts": [...],
 *   "encrypted_payload": "<base64>",
 *   "created_at": "...",
 *   "updated_at": "..."
 * }
 * 
 * Security:
 * - PBKDF2-HMAC-SHA3-256 with 200k iterations
 * - AES-256-GCM (AEAD) for encryption
 * - 16-byte random salt per wallet
 * - 12-byte random nonce per encryption
 * - File permissions 0600 (Unix)
 * - Atomic writes (temp file + rename)
 */
class EncryptedKeystore
{
public:
    EncryptedKeystore();
    
    /**
     * @brief Create new encrypted keystore file.
     * @param path Keystore file path
     * @param payload Plaintext JSON payload
     * @param password User password
     * @return true if created successfully
     */
    static bool create(const QString& path, const QByteArray& payload, const QString& password);
    
    /**
     * @brief Load keystore from file.
     * @param path Keystore file path
     * @return true if loaded successfully
     */
    bool load(const QString& path);
    
    /**
     * @brief Unlock keystore (decrypt payload).
     * @param password User password
     * @param outPayload Decrypted payload (JSON bytes)
     * @return true if unlock successful
     */
    bool unlock(const QString& password, QByteArray& outPayload);
    
    /**
     * @brief Lock keystore (clear decrypted data).
     */
    void lock();
    
    /**
     * @brief Update keystore with new payload.
     * @param payload Plaintext JSON payload
     * @param password User password
     * @return true if saved successfully
     */
    bool save(const QByteArray& payload, const QString& password);
    
    /**
     * @brief Change password (re-encrypt with new password).
     * @param oldPassword Current password
     * @param newPassword New password
     * @return true if changed successfully
     */
    bool changePassword(const QString& oldPassword, const QString& newPassword);
    
    /**
     * @brief Read keystore metadata without decrypting.
     * @return Keystore info
     */
    KeystoreInfo readInfo() const;
    
    /**
     * @brief Get file path.
     * @return Keystore file path
     */
    QString path() const { return m_path; }
    
    /**
     * @brief Check if keystore is loaded.
     * @return true if loaded
     */
    bool isLoaded() const { return m_loaded; }

private:
    /**
     * @brief Derive encryption key from password using PBKDF2-SHA3-256.
     * @param password User password
     * @param salt Salt bytes (16 bytes)
     * @param iterations PBKDF2 iteration count
     * @return 32-byte encryption key
     */
    static QByteArray deriveKey(const QString& password, const QByteArray& salt, int iterations);
    
    /**
     * @brief Encrypt payload using AES-256-GCM.
     * @param plaintext Plaintext bytes
     * @param key 32-byte encryption key
     * @param nonce 12-byte nonce
     * @param outCiphertext Ciphertext output
     * @param outTag 16-byte authentication tag
     * @return true if successful
     */
    static bool encryptAES256GCM(const QByteArray& plaintext, const QByteArray& key,
                                  const QByteArray& nonce, QByteArray& outCiphertext,
                                  QByteArray& outTag);
    
    /**
     * @brief Decrypt ciphertext using AES-256-GCM.
     * @param ciphertext Ciphertext bytes
     * @param key 32-byte encryption key
     * @param nonce 12-byte nonce
     * @param tag 16-byte authentication tag
     * @param outPlaintext Plaintext output
     * @return true if successful
     */
    static bool decryptAES256GCM(const QByteArray& ciphertext, const QByteArray& key,
                                  const QByteArray& nonce, const QByteArray& tag,
                                  QByteArray& outPlaintext);
    
    /**
     * @brief Generate random bytes using CSPRNG.
     * @param size Number of bytes
     * @return Random bytes
     */
    static QByteArray randomBytes(int size);
    
    /**
     * @brief Write JSON to file atomically.
     * @param path File path
     * @param json JSON object
     * @return true if successful
     */
    static bool atomicWrite(const QString& path, const QJsonObject& json);
    
    /**
     * @brief Set file permissions to 0600 (Unix only).
     * @param path File path
     */
    static void setRestrictivePermissions(const QString& path);

    QString m_path;
    QJsonObject m_data;
    bool m_loaded;
};

#endif // ENCRYPTEDKEYSTORE_H
