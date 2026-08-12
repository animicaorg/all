#ifndef WALLETENGINE_H
#define WALLETENGINE_H

#include "AddressBook.h"
#include "BalanceTracker.h"
#include "WalletAccount.h"

#include <QJsonArray>
#include <QJsonObject>
#include <QObject>
#include <QTimer>

class AnimicaRpcClient;
class AnimicaWalletBackend;

class WalletEngine : public QObject
{
    Q_OBJECT

public:
    explicit WalletEngine(AnimicaRpcClient* rpcClient, QObject* parent = nullptr);
    ~WalletEngine() override;

    bool createWallet(const QString& password, const QString& dataDir);
    bool openWallet(const QString& walletFilePath);
    bool unlockWallet(const QString& password);
    void lockWallet();
    bool isLocked() const { return m_locked; }
    bool isLoaded() const;
    QString lastError() const { return m_lastError; }
    bool changePassword(const QString& oldPassword, const QString& newPassword);

    void setAutoLockTimeout(int minutes);
    int autoLockTimeout() const { return m_autoLockMinutes; }
    void resetAutoLock();

    QJsonArray supportedAlgorithms() const;
    WalletAccount createAccount(const QString& label, int algId = 0x1001);
    WalletAccount importAccount(const QJsonObject& json);
    bool importWalletsFile(const QString& sourcePath, bool merge);
    bool removeAccount(const QString& accountId);
    bool renameAccount(const QString& accountId, const QString& newLabel);
    WalletAccount setDefaultAccount(const QString& accountId);
    QList<WalletAccount> listAccounts() const;
    WalletAccount getAccount(const QString& accountId) const;
    QJsonObject exportPublicInfo(const QString& accountId) const;
    bool exportSecretMaterial(const QString& accountId, const QString& destination);

    bool addContact(const QString& label, const QString& address, const QString& note = QString());
    bool updateContact(const QString& address, const QString& label, const QString& note);
    bool removeContact(const QString& address);
    QList<Contact> listContacts(const QString& filter = QString()) const;
    AddressBook::ImportResult importContactsFile(const QString& sourcePath, bool replaceExisting);
    AddressBook::ExportResult exportContactsFile(const QString& destinationPath) const;
    bool validateAddress(const QString& address) const;

    QMap<QString, Balance> getBalances() const;
    Balance getBalance(const QString& address) const;
    void refreshBalances();
    BalanceTracker* balanceTracker() const { return m_balanceTracker; }

    QString signTransaction(const QJsonObject& txJson, const QString& fromAccountId);
    QJsonObject submitTransaction(const QJsonObject& request);
    QJsonObject transactionStatus(const QString& txHash) const;
    QJsonObject transactionDetails(const QString& txHash) const;
    QJsonObject fetchTransactionHistory(const QJsonObject& filters) const;

    QJsonObject contractRead(const QJsonObject& request) const;
    QJsonObject contractWrite(const QJsonObject& request);
    QJsonObject rawContractRead(const QJsonObject& request) const;
    QJsonObject previewContractCall(const QJsonObject& request) const;

    // ==================== ANM Instant (L2) ====================
    //
    // L2 reads go straight over JSON-RPC (shared endpoint, l2_* methods). The
    // send path delegates to the Python bridge, which owns the ML-DSA-65 signer
    // (the SAME key/scheme used for L1) and performs prepare -> sign -> submit.

    /** @brief L2 node/sequencer status summary (empty object on error). */
    QJsonObject l2Status() const;
    /** @brief L2 (instant) balance record for an address (empty object on error). */
    QJsonObject l2Balance(const QString& address) const;
    /** @brief Lifecycle status of an L2 tx by id (empty object on error). */
    QJsonObject l2TransactionStatus(const QString& txid) const;
    /**
     * @brief Send an ANM Instant (L2) transfer: prepare -> sign(signingHash)
     *        -> submit, all inside the wallet backend using the account's
     *        existing ML-DSA-65 key. Returns the bridge result (txid + echoed
     *        recipient/amount/fee for verification), or an empty object on error.
     * @param fromAddress Sender account address (anim1... or 0x-hex).
     * @param toAddress Recipient address (anim1... or 0x-hex).
     * @param amount Human ANM amount as a decimal string (1 ANM = 1e9 nanos).
     */
    QJsonObject sendInstant(const QString& fromAddress, const QString& toAddress, const QString& amount);

    void setRpcEndpoint(const QString& rpcUrl);
    void setExplorerUrl(const QString& explorerUrl);
    QString walletFilePath() const { return m_walletFilePath; }
    QString dataDir() const { return m_dataDir; }

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
    bool reloadAccounts(bool emitChanges);
    WalletAccount walletFromJson(const QJsonObject& json) const;
    void startBalanceTracking();
    void stopBalanceTracking();
    QString algorithmNameForId(int algId) const;
    QJsonObject backendResult(const QString& operation, const QJsonObject& args = QJsonObject(), int timeoutMs = 60000) const;

    AnimicaRpcClient* m_rpcClient;
    AnimicaWalletBackend* m_backend;
    AddressBook* m_addressBook;
    BalanceTracker* m_balanceTracker;
    QList<WalletAccount> m_accounts;
    QTimer m_autoLockTimer;
    int m_autoLockMinutes;
    bool m_loaded;
    bool m_locked;
    QString m_walletFilePath;
    QString m_dataDir;
    QString m_addressBookPath;
    mutable QString m_lastError;
};

#endif // WALLETENGINE_H
