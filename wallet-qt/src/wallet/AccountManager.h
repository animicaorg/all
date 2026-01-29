#ifndef ACCOUNTMANAGER_H
#define ACCOUNTMANAGER_H

#include "WalletAccount.h"
#include <QObject>
#include <QList>
#include <QJsonObject>

/**
 * @brief Manages wallet accounts (CRUD operations).
 * 
 * Handles account creation, import, export, and metadata updates.
 * Uses Python subprocess for Dilithium3 keygen and address derivation.
 */
class AccountManager : public QObject
{
    Q_OBJECT

public:
    explicit AccountManager(QObject* parent = nullptr);
    
    /**
     * @brief Create new account with generated keypair.
     * @param label User-friendly account name
     * @param algId Algorithm ID (default: 0x1001 = Dilithium3)
     * @return New WalletAccount or invalid account on error
     */
    WalletAccount createAccount(const QString& label, int algId = 0x1001);
    
    /**
     * @brief Import account from JSON.
     * @param json Account JSON (with secret key)
     * @return Imported WalletAccount or invalid account on error
     */
    WalletAccount importAccount(const QJsonObject& json);
    
    /**
     * @brief Rename account.
     * @param accountId Account UUID
     * @param newLabel New label
     * @return true if successful
     */
    bool renameAccount(const QString& accountId, const QString& newLabel);
    
    /**
     * @brief Set default account.
     * @param accountId Account UUID
     * @return Updated account
     */
    WalletAccount setDefault(const QString& accountId);
    
    /**
     * @brief Update last used timestamp.
     * @param accountId Account UUID
     */
    void updateLastUsed(const QString& accountId);
    
    /**
     * @brief Get account by ID.
     * @param accountId Account UUID
     * @return WalletAccount or invalid account if not found
     */
    WalletAccount getAccount(const QString& accountId) const;
    
    /**
     * @brief Get all accounts.
     * @return List of accounts
     */
    QList<WalletAccount> listAccounts() const;
    
    /**
     * @brief Load accounts from decrypted payload.
     * @param payload Decrypted JSON payload
     * @param publicAccounts Public account metadata
     */
    void loadAccounts(const QByteArray& payload, const QJsonArray& publicAccounts);
    
    /**
     * @brief Save accounts to JSON payload.
     * @param outPayload Output JSON payload
     * @param outPublicAccounts Output public account metadata
     */
    void saveAccounts(QByteArray& outPayload, QJsonArray& outPublicAccounts);
    
    /**
     * @brief Clear all accounts (lock).
     */
    void clearAccounts();
    
signals:
    void accountAdded(const WalletAccount& account);
    void accountUpdated(const WalletAccount& account);
    void accountRemoved(const QString& accountId);

private:
    /**
     * @brief Generate keypair via Python subprocess.
     * @param algId Algorithm ID
     * @param outPublicKey Public key output
     * @param outSecretKey Secret key output
     * @return true if successful
     */
    bool generateKeypair(int algId, QByteArray& outPublicKey, QByteArray& outSecretKey);
    
    /**
     * @brief Derive bech32m address via Python subprocess.
     * @param publicKey Public key bytes
     * @param algId Algorithm ID
     * @return bech32m address or empty string on error
     */
    QString deriveAddress(const QByteArray& publicKey, int algId);
    
    /**
     * @brief Get algorithm name from ID.
     * @param algId Algorithm ID
     * @return Algorithm name (e.g., "dilithium3")
     */
    QString algIdToName(int algId) const;

    QList<WalletAccount> m_accounts;
};

#endif // ACCOUNTMANAGER_H
