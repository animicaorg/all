#ifndef WALLETACCOUNT_H
#define WALLETACCOUNT_H

#include <QString>
#include <QByteArray>
#include <QDateTime>
#include <QJsonObject>

/**
 * @brief Account data structure for wallet.
 * 
 * Represents a single account with public/secret key pair.
 * Supports serialization to/from JSON for storage.
 */
struct WalletAccount {
    QString accountId;          // UUID v4
    QString label;              // User-friendly name
    QString address;            // bech32m (anim1...)
    int algId;                  // 0x1001 = Dilithium3
    QString algName;            // "dilithium3"
    QByteArray publicKey;       // Raw bytes
    QByteArray secretKey;       // Raw bytes (only when unlocked)
    QDateTime createdAt;
    QDateTime lastUsedAt;
    bool isDefault;

    WalletAccount();
    
    /**
     * @brief Serialize to JSON (public info only).
     * @return JSON object with public account data
     */
    QJsonObject toPublicJson() const;
    
    /**
     * @brief Serialize to JSON (includes secret key).
     * @return JSON object with full account data
     */
    QJsonObject toSecretJson() const;
    
    /**
     * @brief Deserialize from JSON (public info).
     * @param json JSON object
     * @return WalletAccount with public data populated
     */
    static WalletAccount fromPublicJson(const QJsonObject& json);
    
    /**
     * @brief Deserialize from JSON (includes secret key).
     * @param json JSON object
     * @return WalletAccount with full data populated
     */
    static WalletAccount fromSecretJson(const QJsonObject& json);
    
    /**
     * @brief Clear secret key from memory.
     */
    void clearSecrets();
    
    /**
     * @brief Check if account has secret key loaded.
     * @return true if secretKey is not empty
     */
    bool hasSecretKey() const;
};

#endif // WALLETACCOUNT_H
