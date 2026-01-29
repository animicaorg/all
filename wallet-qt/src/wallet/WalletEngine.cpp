#include "WalletEngine.h"
#include "../rpc/AnimicaRpcClient.h"
#include "../platform/AppPaths.h"
#include <QDir>
#include <QJsonDocument>
#include <QJsonArray>
#include <QProcess>
#include <QDebug>

WalletEngine::WalletEngine(AnimicaRpcClient* rpcClient, QObject* parent)
    : QObject(parent)
    , m_rpcClient(rpcClient)
    , m_keystore(new EncryptedKeystore())
    , m_accountManager(new AccountManager(this))
    , m_addressBook(new AddressBook(this))
    , m_balanceTracker(new BalanceTracker(rpcClient, this))
    , m_autoLockMinutes(15)
    , m_locked(true)
{
    // Connect auto-lock timer
    connect(&m_autoLockTimer, &QTimer::timeout, this, &WalletEngine::handleAutoLock);
    m_autoLockTimer.setSingleShot(true);
    
    // Forward signals from components
    connect(m_accountManager, &AccountManager::accountAdded, this, &WalletEngine::accountAdded);
    connect(m_accountManager, &AccountManager::accountUpdated, this, &WalletEngine::accountUpdated);
    connect(m_accountManager, &AccountManager::accountRemoved, this, &WalletEngine::accountRemoved);
    
    connect(m_addressBook, &AddressBook::contactAdded, this, &WalletEngine::contactAdded);
    connect(m_addressBook, &AddressBook::contactUpdated, this, &WalletEngine::contactUpdated);
    connect(m_addressBook, &AddressBook::contactRemoved, this, &WalletEngine::contactRemoved);
    
    connect(m_balanceTracker, &BalanceTracker::balanceUpdated, this, &WalletEngine::balanceUpdated);
    connect(m_balanceTracker, &BalanceTracker::syncStatusChanged, this, &WalletEngine::syncStatusChanged);
    connect(m_balanceTracker, &BalanceTracker::error, this, &WalletEngine::error);
}

WalletEngine::~WalletEngine()
{
    if (!m_locked) {
        lockWallet();
    }
    delete m_keystore;
}

bool WalletEngine::createWallet(const QString& password, const QString& dataDir)
{
    m_dataDir = dataDir;
    
    // Ensure directory exists
    QDir dir;
    if (!dir.mkpath(dataDir)) {
        emit error("Failed to create data directory");
        return false;
    }
    
    // Create empty wallet payload
    QJsonObject payload;
    payload["accounts"] = QJsonArray();
    payload["master_seed"] = QJsonValue::Null;
    payload["address_book_notes"] = QJsonObject();
    
    QJsonDocument doc(payload);
    QByteArray payloadBytes = doc.toJson(QJsonDocument::Compact);
    
    // Create keystore
    QString keystorePath = dataDir + "/keystore.json";
    if (!EncryptedKeystore::create(keystorePath, payloadBytes, password)) {
        emit error("Failed to create keystore");
        return false;
    }
    
    // Load keystore
    if (!m_keystore->load(keystorePath)) {
        emit error("Failed to load created keystore");
        return false;
    }
    
    // Create address book file
    m_addressBookPath = dataDir + "/address_book.json";
    m_addressBook->load(m_addressBookPath);
    
    return true;
}

bool WalletEngine::openWallet(const QString& keystorePath)
{
    if (!m_keystore->load(keystorePath)) {
        emit error("Failed to load keystore");
        return false;
    }
    
    // Derive data directory from keystore path
    QFileInfo fileInfo(keystorePath);
    m_dataDir = fileInfo.absolutePath();
    m_addressBookPath = m_dataDir + "/address_book.json";
    
    // Load address book
    m_addressBook->load(m_addressBookPath);
    
    return true;
}

bool WalletEngine::unlockWallet(const QString& password)
{
    if (!m_keystore->isLoaded()) {
        emit error("No wallet loaded");
        return false;
    }
    
    // Decrypt keystore
    QByteArray payload;
    if (!m_keystore->unlock(password, payload)) {
        emit error("Incorrect password");
        return false;
    }
    
    // Parse payload JSON
    QJsonDocument doc = QJsonDocument::fromJson(payload);
    if (!doc.isObject()) {
        emit error("Invalid wallet payload");
        return false;
    }
    
    // TODO: Load public accounts metadata from keystore
    // For now, pass empty array - accounts will have minimal metadata
    QJsonArray publicAccounts;
    m_accountManager->loadAccounts(payload, publicAccounts);
    
    m_locked = false;
    emit walletUnlocked();
    
    // Start balance tracking
    startBalanceTracking();
    
    // Start auto-lock timer if enabled
    if (m_autoLockMinutes > 0) {
        m_autoLockTimer.start(m_autoLockMinutes * 60 * 1000);
    }
    
    return true;
}

void WalletEngine::lockWallet()
{
    if (m_locked) {
        return;
    }
    
    // Stop balance tracking
    stopBalanceTracking();
    
    // Clear accounts from memory
    m_accountManager->clearAccounts();
    
    // Lock keystore
    m_keystore->lock();
    
    m_locked = true;
    m_autoLockTimer.stop();
    
    emit walletLocked();
}

bool WalletEngine::isLoaded() const
{
    return m_keystore && m_keystore->isLoaded();
}

bool WalletEngine::changePassword(const QString& oldPassword, const QString& newPassword)
{
    if (!m_keystore->isLoaded()) {
        emit error("No wallet loaded");
        return false;
    }
    
    if (!m_keystore->changePassword(oldPassword, newPassword)) {
        emit error("Failed to change password");
        return false;
    }
    
    return true;
}

void WalletEngine::setAutoLockTimeout(int minutes)
{
    m_autoLockMinutes = minutes;
    
    if (!m_locked && minutes > 0) {
        m_autoLockTimer.start(minutes * 60 * 1000);
    } else {
        m_autoLockTimer.stop();
    }
}

void WalletEngine::resetAutoLock()
{
    if (!m_locked && m_autoLockMinutes > 0) {
        m_autoLockTimer.start(m_autoLockMinutes * 60 * 1000);
    }
}

void WalletEngine::handleAutoLock()
{
    qDebug() << "Auto-lock timeout reached";
    lockWallet();
}

WalletAccount WalletEngine::createAccount(const QString& label)
{
    if (m_locked) {
        emit error("Wallet is locked");
        return WalletAccount();
    }
    
    WalletAccount account = m_accountManager->createAccount(label);
    
    if (!account.accountId.isEmpty()) {
        saveWallet();
        resetAutoLock();
    }
    
    return account;
}

WalletAccount WalletEngine::importAccount(const QJsonObject& json)
{
    if (m_locked) {
        emit error("Wallet is locked");
        return WalletAccount();
    }
    
    WalletAccount account = m_accountManager->importAccount(json);
    
    if (!account.accountId.isEmpty()) {
        saveWallet();
        resetAutoLock();
    }
    
    return account;
}

bool WalletEngine::removeAccount(const QString& accountId)
{
    if (m_locked) {
        emit error("Wallet is locked");
        return false;
    }
    
    // Not implemented in AccountManager - would need to add this method
    emit error("Remove account not implemented");
    return false;
}

bool WalletEngine::renameAccount(const QString& accountId, const QString& newLabel)
{
    if (m_locked) {
        emit error("Wallet is locked");
        return false;
    }
    
    if (m_accountManager->renameAccount(accountId, newLabel)) {
        saveWallet();
        resetAutoLock();
        return true;
    }
    
    return false;
}

WalletAccount WalletEngine::setDefaultAccount(const QString& accountId)
{
    if (m_locked) {
        emit error("Wallet is locked");
        return WalletAccount();
    }
    
    WalletAccount account = m_accountManager->setDefault(accountId);
    
    if (!account.accountId.isEmpty()) {
        saveWallet();
        resetAutoLock();
    }
    
    return account;
}

QList<WalletAccount> WalletEngine::listAccounts() const
{
    return m_accountManager->listAccounts();
}

WalletAccount WalletEngine::getAccount(const QString& accountId) const
{
    return m_accountManager->getAccount(accountId);
}

bool WalletEngine::addContact(const QString& label, const QString& address, const QString& note)
{
    return m_addressBook->addContact(label, address, note);
}

bool WalletEngine::updateContact(const QString& address, const QString& label, const QString& note)
{
    return m_addressBook->updateContact(address, label, note);
}

bool WalletEngine::removeContact(const QString& address)
{
    return m_addressBook->removeContact(address);
}

QList<Contact> WalletEngine::listContacts(const QString& filter) const
{
    return m_addressBook->listContacts(filter);
}

QMap<QString, Balance> WalletEngine::getBalances() const
{
    return m_balanceTracker->getBalances();
}

Balance WalletEngine::getBalance(const QString& address) const
{
    return m_balanceTracker->getBalance(address);
}

void WalletEngine::refreshBalances()
{
    m_balanceTracker->refresh();
    resetAutoLock();
}

QString WalletEngine::signTransaction(const QJsonObject& txJson, const QString& fromAccountId)
{
    if (m_locked) {
        emit error("Wallet is locked");
        return QString();
    }
    
    WalletAccount account = m_accountManager->getAccount(fromAccountId);
    if (account.accountId.isEmpty() || !account.hasSecretKey()) {
        emit error("Account not found or missing secret key");
        return QString();
    }
    
    // TODO: Implement proper transaction signing with domain separation
    // This requires:
    // 1. Serialize transaction to canonical CBOR format
    // 2. Call pq.py.sign.sign_detached with domain="tx/sign", chain_id, etc.
    // 3. Build SignedTransaction envelope (see omni_sdk for format)
    // 4. Return hex-encoded signed transaction
    
    // Update last used timestamp
    m_accountManager->updateLastUsed(fromAccountId);
    saveWallet();
    resetAutoLock();
    
    emit error("Transaction signing not yet implemented");
    return QString();
}

bool WalletEngine::saveWallet()
{
    if (!m_keystore->isLoaded()) {
        return false;
    }
    
    // Serialize accounts to payload
    QByteArray payload;
    QJsonArray publicAccounts;
    m_accountManager->saveAccounts(payload, publicAccounts);
    
    // For now, we use a simple password re-prompt approach
    // In production, would cache the password or use a key derivation
    // For this implementation, we'll assume the password is available
    // This is a limitation - proper implementation would need password caching
    
    emit error("Save wallet requires password (not implemented in this version)");
    return false;
}

void WalletEngine::startBalanceTracking()
{
    QStringList addresses;
    for (const WalletAccount& account : m_accountManager->listAccounts()) {
        addresses.append(account.address);
    }
    
    if (!addresses.isEmpty()) {
        m_balanceTracker->startTracking(addresses);
    }
}

void WalletEngine::stopBalanceTracking()
{
    m_balanceTracker->stopTracking();
}
