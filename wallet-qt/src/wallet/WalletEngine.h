#ifndef WALLETENGINE_H
#define WALLETENGINE_H

#include "WalletAccount.h"
#include "EncryptedKeystore.h"
#include "AccountManager.h"
#include "AddressBook.h"
#include "BalanceTracker.h"
#include <QObject>
#include <QTimer>
#include <QString>

// Forward declaration
class AnimicaRpcClient;

/**
 * @brief Main wallet engine coordinator.
 * 
 * State machine:
 * - Locked: No keys in memory, all operations fail
 * - Unlocked: Keys loaded, operations permitted
 * 
 * Features:
 * - Encrypted keystore management
 * - Account CRUD operations
 * - Address book management
 * - Balance tracking
 * - Auto-lock timer
 * - Transaction signing coordination
 */
class WalletEngine : public QObject
{
    Q_OBJECT

public:
    explicit WalletEngine(AnimicaRpcClient* rpcClient, QObject* parent = nullptr);
    ~WalletEngine();
    
    // ==================== Wallet Management ====================
    
    /**
     * @brief Create new wallet.
     * @param password Wallet password
     * @param dataDir Data directory path
     * @return true if created successfully
     */
    bool createWallet(const QString& password, const QString& dataDir);
    
    /**
     * @brief Open existing wallet.
     * @param keystorePath Path to keystore.json
     * @return true if opened successfully
     */
    bool openWallet(const QString& keystorePath);
    
    /**
     * @brief Unlock wallet.
     * @param password Wallet password
     * @return true if unlocked successfully
     */
    bool unlockWallet(const QString& password);
    
    /**
     * @brief Lock wallet (clear secrets from memory).
     */
    void lockWallet();
    
    /**
     * @brief Check if wallet is locked.
     * @return true if locked
     */
    bool isLocked() const { return m_locked; }
    
    /**
     * @brief Check if wallet is loaded.
     * @return true if wallet file is loaded
     */
    bool isLoaded() const;
    
    /**
     * @brief Change wallet password.
     * @param oldPassword Current password
     * @param newPassword New password
     * @return true if changed successfully
     */
    bool changePassword(const QString& oldPassword, const QString& newPassword);
    
    // ==================== Auto-Lock ====================
    
    /**
     * @brief Set auto-lock timeout.
     * @param minutes Timeout in minutes (0 = disabled)
     */
    void setAutoLockTimeout(int minutes);
    
    /**
     * @brief Get auto-lock timeout.
     * @return Timeout in minutes
     */
    int autoLockTimeout() const { return m_autoLockMinutes; }
    
    /**
     * @brief Reset auto-lock timer (activity detected).
     */
    void resetAutoLock();
    
    // ==================== Account Management ====================
    
    /**
     * @brief Create new account.
     * @param label Account label
     * @return New account or invalid account on error
     */
    WalletAccount createAccount(const QString& label);
    
    /**
     * @brief Import account from JSON.
     * @param json Account JSON
     * @return Imported account or invalid account on error
     */
    WalletAccount importAccount(const QJsonObject& json);
    
    /**
     * @brief Remove account.
     * @param accountId Account UUID
     * @return true if removed successfully
     */
    bool removeAccount(const QString& accountId);
    
    /**
     * @brief Rename account.
     * @param accountId Account UUID
     * @param newLabel New label
     * @return true if renamed successfully
     */
    bool renameAccount(const QString& accountId, const QString& newLabel);
    
    /**
     * @brief Set default account.
     * @param accountId Account UUID
     * @return Updated account
     */
    WalletAccount setDefaultAccount(const QString& accountId);
    
    /**
     * @brief List all accounts.
     * @return List of accounts
     */
    QList<WalletAccount> listAccounts() const;
    
    /**
     * @brief Get account by ID.
     * @param accountId Account UUID
     * @return Account or invalid account if not found
     */
    WalletAccount getAccount(const QString& accountId) const;
    
    // ==================== Address Book ====================
    
    /**
     * @brief Add contact to address book.
     * @param label Contact name
     * @param address Bech32m address
     * @param note Optional note
     * @return true if added successfully
     */
    bool addContact(const QString& label, const QString& address, const QString& note = QString());
    
    /**
     * @brief Update contact.
     * @param address Contact address
     * @param label New label
     * @param note New note
     * @return true if updated successfully
     */
    bool updateContact(const QString& address, const QString& label, const QString& note);
    
    /**
     * @brief Remove contact.
     * @param address Contact address
     * @return true if removed successfully
     */
    bool removeContact(const QString& address);
    
    /**
     * @brief List contacts.
     * @param filter Optional filter string
     * @return List of contacts
     */
    QList<Contact> listContacts(const QString& filter = QString()) const;
    
    // ==================== Balance Tracking ====================
    
    /**
     * @brief Get account balances.
     * @return Map of address -> balance
     */
    QMap<QString, Balance> getBalances() const;
    
    /**
     * @brief Get balance for specific address.
     * @param address Bech32m address
     * @return Balance
     */
    Balance getBalance(const QString& address) const;
    
    /**
     * @brief Refresh balances immediately.
     */
    void refreshBalances();
    
    /**
     * @brief Get balance tracker instance.
     * @return Pointer to balance tracker
     */
    BalanceTracker* balanceTracker() const { return m_balanceTracker; }
    
    // ==================== Transaction Signing ====================
    
    /**
     * @brief Sign transaction.
     * @param txJson Transaction JSON (unsigned)
     * @param fromAccountId Account UUID to sign with
     * @return Signed transaction hex or empty on error
     */
    QString signTransaction(const QJsonObject& txJson, const QString& fromAccountId);
    
signals:
    void walletLocked();
    void walletUnlocked();
    void accountAdded(const WalletAccount& account);
    void accountUpdated(const WalletAccount& account);
    void accountRemoved(const QString& accountId);
    void contactAdded(const Contact& contact);
    void contactUpdated(const Contact& contact);
    void contactRemoved(const QString& address);
    void balanceUpdated(const QString& address, const Balance& balance);
    void syncStatusChanged(bool syncing);
    void error(const QString& message);

private slots:
    void handleAutoLock();

private:
    bool saveWallet();
    void startBalanceTracking();
    void stopBalanceTracking();

    AnimicaRpcClient* m_rpcClient;
    EncryptedKeystore* m_keystore;
    AccountManager* m_accountManager;
    AddressBook* m_addressBook;
    BalanceTracker* m_balanceTracker;
    
    QTimer m_autoLockTimer;
    int m_autoLockMinutes;
    bool m_locked;
    QString m_dataDir;
    QString m_addressBookPath;
};

#endif // WALLETENGINE_H
