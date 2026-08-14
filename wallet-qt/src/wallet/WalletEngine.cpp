#include "WalletEngine.h"

#include "AnimicaWalletBackend.h"
#include "WalletSecuritySettings.h"
#include "../rpc/AnimicaRpcClient.h"
#include "../rpc/RpcSettings.h"

#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QSaveFile>
#include <QSet>

namespace {

QByteArray canonicalWalletStorePayload()
{
    const QString timestamp = QDateTime::currentDateTimeUtc().toString(Qt::ISODate);
    QJsonObject store;
    store["format"] = QStringLiteral("animica.wallets");
    store["version"] = 2;
    store["created_at"] = timestamp;
    store["updated_at"] = timestamp;
    store["wallets"] = QJsonArray();
    store["default"] = QJsonValue::Null;

    QByteArray payload = QJsonDocument(store).toJson(QJsonDocument::Indented);
    if (!payload.endsWith('\n')) {
        payload.append('\n');
    }
    return payload;
}

bool writeCanonicalWalletStore(const QString& walletFilePath, bool overwriteExisting, QString& errorMessage)
{
    const QFileInfo walletInfo(walletFilePath);
    QDir walletDir(walletInfo.absolutePath());
    if (!walletDir.exists() && !walletDir.mkpath(".")) {
        errorMessage = QString("Failed to create wallet data directory: %1").arg(walletInfo.absolutePath());
        return false;
    }

    if (!overwriteExisting && walletInfo.exists()) {
        return true;
    }

    QSaveFile walletFile(walletInfo.absoluteFilePath());
    if (!walletFile.open(QIODevice::WriteOnly | QIODevice::Text)) {
        errorMessage = QString("Failed to create wallets.json: %1").arg(walletFile.errorString());
        return false;
    }

    const QByteArray payload = canonicalWalletStorePayload();

    if (walletFile.write(payload) != payload.size()) {
        errorMessage = QString("Failed to write wallets.json: %1").arg(walletFile.errorString());
        walletFile.cancelWriting();
        return false;
    }

    if (!walletFile.commit()) {
        errorMessage = QString("Failed to finalize wallets.json: %1").arg(walletFile.errorString());
        return false;
    }

#ifndef Q_OS_WIN
    QFile::setPermissions(walletInfo.absoluteFilePath(), QFile::ReadOwner | QFile::WriteOwner);
#endif

    return true;
}

bool ensureCanonicalWalletStoreExists(const QString& walletFilePath, QString& errorMessage)
{
    return writeCanonicalWalletStore(walletFilePath, false, errorMessage);
}

bool resetCanonicalWalletStore(const QString& walletFilePath, QString& errorMessage)
{
    return writeCanonicalWalletStore(walletFilePath, true, errorMessage);
}

bool isWalletStoreParseFailure(const QString& errorMessage)
{
    const QString lowered = errorMessage.toLower();
    return lowered.contains("failed to parse wallet store")
        || lowered.contains("failed to parse wallet file")
        || lowered.contains("invalid json")
        || lowered.contains("unsupported wallets.json")
        || lowered.contains("wallets must be a list");
}

bool backupWalletStore(const QString& walletFilePath, QString& backupPath, QString& errorMessage)
{
    const QFileInfo walletInfo(walletFilePath);
    if (!walletInfo.exists()) {
        errorMessage = QString("Wallet store does not exist: %1").arg(walletFilePath);
        return false;
    }

    const QString timestamp = QDateTime::currentDateTimeUtc().toString("yyyyMMddTHHmmssZ");
    backupPath = QDir(walletInfo.absolutePath()).filePath(
        QString("%1.corrupt.%2.bak").arg(walletInfo.fileName(), timestamp)
    );
    if (QFileInfo::exists(backupPath)) {
        const QString uniquifier = QDateTime::currentDateTimeUtc().toString("zzz");
        backupPath = QDir(walletInfo.absolutePath()).filePath(
            QString("%1.corrupt.%2-%3.bak").arg(walletInfo.fileName(), timestamp, uniquifier)
        );
    }

    QFile source(walletInfo.absoluteFilePath());
    if (!source.copy(backupPath)) {
        errorMessage = QString("Failed to back up wallet store to %1: %2").arg(backupPath, source.errorString());
        return false;
    }

#ifndef Q_OS_WIN
    QFile::setPermissions(backupPath, QFile::ReadOwner | QFile::WriteOwner);
#endif
    return true;
}

} // namespace

WalletEngine::WalletEngine(AnimicaRpcClient* rpcClient, QObject* parent)
    : QObject(parent)
    , m_rpcClient(rpcClient)
    , m_backend(new AnimicaWalletBackend(this))
    , m_addressBook(new AddressBook(this))
    , m_balanceTracker(new BalanceTracker(rpcClient, this))
    , m_autoLockMinutes(0)
    , m_loaded(false)
    , m_locked(true)
{
    connect(&m_autoLockTimer, &QTimer::timeout, this, &WalletEngine::handleAutoLock);
    m_autoLockTimer.setSingleShot(true);

    connect(m_addressBook, &AddressBook::contactAdded, this, &WalletEngine::contactAdded);
    connect(m_addressBook, &AddressBook::contactUpdated, this, &WalletEngine::contactUpdated);
    connect(m_addressBook, &AddressBook::contactRemoved, this, &WalletEngine::contactRemoved);

    connect(m_balanceTracker, &BalanceTracker::balanceUpdated, this, &WalletEngine::balanceUpdated);
    connect(m_balanceTracker, &BalanceTracker::syncStatusChanged, this, &WalletEngine::syncStatusChanged);
    connect(m_balanceTracker, &BalanceTracker::error, this, &WalletEngine::error);

    if (m_rpcClient) {
        const QString endpoint = m_rpcClient->endpoint().trimmed().isEmpty()
            ? RpcSettings::canonicalRpcUrl()
            : m_rpcClient->endpoint();
        setRpcEndpoint(endpoint);
    }
}

WalletEngine::~WalletEngine()
{
    stopBalanceTracking();
}

QJsonObject WalletEngine::backendResult(const QString& operation, const QJsonObject& args, int timeoutMs) const
{
    const QJsonObject response = m_backend->call(operation, args, timeoutMs);
    if (!response.value("ok").toBool()) {
        const QJsonObject error = response.value("error").toObject();
        const QString message = error.value("message").toString("Wallet backend operation failed.");
        const QString details = error.value("details").toString().trimmed();
        if (!details.isEmpty() && !message.contains(details)) {
            m_lastError = QString("%1\n\nDetails: %2").arg(message, details);
        } else {
            m_lastError = message;
        }
        emit const_cast<WalletEngine*>(this)->error(m_lastError);
    } else {
        m_lastError.clear();
    }
    return response;
}

QString WalletEngine::algorithmNameForId(int algId) const
{
    switch (algId) {
    case 0x1001:
        return "dilithium3";
    case 0x1002:
        return "sphincs_shake_128s";
    case 0x1003:
        return "ml_dsa_65";
    default:
        return "ml_dsa_65";
    }
}

WalletAccount WalletEngine::walletFromJson(const QJsonObject& json) const
{
    WalletAccount account;
    account.accountId = json.value("wallet_id").toString(json.value("address").toString());
    account.label = json.value("label").toString();
    account.address = json.value("address").toString();
    account.algId = json.value("algorithm_id").toInt();
    account.algName = json.value("algorithm").toString();
    account.publicKey = QByteArray::fromHex(json.value("public_key_hex").toString().toLatin1());
    account.createdAt = QDateTime::fromString(json.value("created_at").toString(), Qt::ISODate);
    if (!account.createdAt.isValid()) {
        account.createdAt = QDateTime::currentDateTimeUtc();
    }
    account.lastUsedAt = account.createdAt;
    account.isDefault = json.value("is_default").toBool();
    return account;
}

bool WalletEngine::reloadAccounts(bool emitChanges)
{
    const QJsonObject response = backendResult("list_wallets");
    if (!response.value("ok").toBool()) {
        return false;
    }

    const QJsonArray wallets = response.value("result").toObject().value("wallets").toArray();
    QMap<QString, WalletAccount> oldAccounts;
    for (const WalletAccount& account : m_accounts) {
        oldAccounts.insert(account.accountId, account);
    }

    QList<WalletAccount> nextAccounts;
    QSet<QString> seenIds;
    for (const QJsonValue& value : wallets) {
        if (!value.isObject()) {
            continue;
        }
        const WalletAccount account = walletFromJson(value.toObject());
        nextAccounts.append(account);
        seenIds.insert(account.accountId);
    }

    m_accounts = nextAccounts;

    if (emitChanges) {
        for (auto it = oldAccounts.constBegin(); it != oldAccounts.constEnd(); ++it) {
            if (!seenIds.contains(it.key())) {
                emit accountRemoved(it.key());
            }
        }
        for (const WalletAccount& account : m_accounts) {
            if (!oldAccounts.contains(account.accountId)) {
                emit accountAdded(account);
            } else {
                emit accountUpdated(account);
            }
        }
    }

    startBalanceTracking();
    return true;
}

bool WalletEngine::createWallet(const QString& password, const QString& dataDir)
{
    Q_UNUSED(password);

    QDir dir(dataDir);
    if (!dir.exists() && !dir.mkpath(".")) {
        m_lastError = "Failed to create wallet data directory.";
        emit error("Failed to create wallet data directory.");
        return false;
    }
    return openWallet(dir.filePath("wallets.json"));
}

bool WalletEngine::openWallet(const QString& walletFilePath)
{
    QFileInfo info(walletFilePath);
    const QString nextWalletFilePath = info.absoluteFilePath();
    const QString nextDataDir = info.absolutePath();
    const QString nextAddressBookPath = QDir(nextDataDir).filePath("address_book.json");

    QString errorMessage;
    if (!ensureCanonicalWalletStoreExists(nextWalletFilePath, errorMessage)) {
        m_lastError = errorMessage;
        emit error(errorMessage);
        return false;
    }

    if (AnimicaWalletBackend::findPythonInterpreter().trimmed().isEmpty()) {
        m_lastError = QString(
            "Bundled Python runtime was not found for the Animica wallet backend.\n\n"
            "The canonical wallets.json store exists at:\n%1\n\n"
            "Reinstall the wallet package, or set ANIMICA_WALLET_PYTHON to a valid Python 3.10+ interpreter."
        ).arg(nextWalletFilePath);
        emit error(m_lastError);
        return false;
    }

    const QString previousBackendWalletFile = m_backend->walletFile();
    const QString previousWalletFilePath = m_walletFilePath;
    const QString previousDataDir = m_dataDir;
    const QString previousAddressBookPath = m_addressBookPath;
    const bool previousLoaded = m_loaded;
    const bool previousLocked = m_locked;
    const QList<WalletAccount> previousAccounts = m_accounts;

    m_backend->setWalletFile(nextWalletFilePath);

    QJsonObject response = backendResult("init_store");
    if (!response.value("ok").toBool() && isWalletStoreParseFailure(m_lastError)) {
        QString backupPath;
        QString recoveryError;
        if (backupWalletStore(nextWalletFilePath, backupPath, recoveryError)
            && resetCanonicalWalletStore(nextWalletFilePath, recoveryError)) {
            response = backendResult("init_store");
            if (!response.value("ok").toBool()) {
                m_lastError = QString(
                    "Recovered unreadable wallets.json to a fresh store, but backend initialization still failed.\n"
                    "Backup saved at: %1\n\n%2"
                ).arg(backupPath, m_lastError);
            }
        } else {
            const QString originalError = m_lastError;
            m_lastError = QString(
                "%1\n\nAutomatic recovery failed: %2"
            ).arg(originalError, recoveryError);
        }
    }
    if (!response.value("ok").toBool()) {
        m_backend->setWalletFile(previousBackendWalletFile);
        m_walletFilePath = previousWalletFilePath;
        m_dataDir = previousDataDir;
        m_addressBookPath = previousAddressBookPath;
        m_loaded = previousLoaded;
        m_locked = previousLocked;
        m_accounts = previousAccounts;
        return false;
    }

    m_walletFilePath = nextWalletFilePath;
    m_dataDir = nextDataDir;
    m_addressBookPath = nextAddressBookPath;
    m_loaded = true;

    m_locked = false;
    if (!reloadAccounts(false)) {
        m_backend->setWalletFile(previousBackendWalletFile);
        m_walletFilePath = previousWalletFilePath;
        m_dataDir = previousDataDir;
        m_addressBookPath = previousAddressBookPath;
        m_loaded = previousLoaded;
        m_locked = previousLocked;
        m_accounts = previousAccounts;
        return false;
    }

    if (!m_addressBook->load(m_addressBookPath)) {
        m_lastError = "Failed to load address book.";
        emit error("Failed to load address book.");
    }

    m_lastError.clear();
    if (WalletSecuritySettings::walletEncryptionEnabled()) {
        lockWallet();
        return true;
    }
    emit walletUnlocked();
    resetAutoLock();
    return true;
}

bool WalletEngine::unlockWallet(const QString& password)
{
    if (!m_loaded) {
        m_lastError = "No wallet store is loaded.";
        emit error("No wallet store is loaded.");
        return false;
    }
    if (!m_locked) {
        m_lastError.clear();
        return true;
    }

    if (WalletSecuritySettings::walletEncryptionEnabled()) {
        if (!WalletSecuritySettings::hasTransferPassword()) {
            m_lastError = "Wallet encryption is enabled, but no transfer password is configured.";
            emit error(m_lastError);
            return false;
        }
        if (!WalletSecuritySettings::verifyTransferPassword(password)) {
            m_lastError = "Wallet password is incorrect.";
            emit error(m_lastError);
            return false;
        }
    }

    m_locked = false;
    if (!reloadAccounts(false)) {
        m_locked = true;
        return false;
    }
    m_lastError.clear();
    emit walletUnlocked();
    resetAutoLock();
    return true;
}

void WalletEngine::lockWallet()
{
    if (m_locked) {
        return;
    }
    stopBalanceTracking();
    m_accounts.clear();
    m_locked = true;
    emit walletLocked();
}

bool WalletEngine::isLoaded() const
{
    return m_loaded;
}

bool WalletEngine::changePassword(const QString& oldPassword, const QString& newPassword)
{
    if (newPassword.size() < WalletSecuritySettings::kMinPasswordLength) {
        m_lastError = QString("Password must be at least %1 characters.")
            .arg(WalletSecuritySettings::kMinPasswordLength);
        emit error(m_lastError);
        return false;
    }

    if (WalletSecuritySettings::hasTransferPassword()
        && !WalletSecuritySettings::verifyTransferPassword(oldPassword)) {
        m_lastError = "Current password is incorrect.";
        emit error(m_lastError);
        return false;
    }

    if (!WalletSecuritySettings::setTransferPassword(newPassword)) {
        m_lastError = "Failed to update wallet password.";
        emit error(m_lastError);
        return false;
    }

    m_lastError.clear();
    return true;
}

void WalletEngine::setAutoLockTimeout(int minutes)
{
    m_autoLockMinutes = qMax(0, minutes);
    resetAutoLock();
}

void WalletEngine::resetAutoLock()
{
    if (m_locked || m_autoLockMinutes <= 0) {
        m_autoLockTimer.stop();
        return;
    }
    m_autoLockTimer.start(m_autoLockMinutes * 60 * 1000);
}

void WalletEngine::handleAutoLock()
{
    lockWallet();
}

QJsonArray WalletEngine::supportedAlgorithms() const
{
    const QJsonObject response = backendResult("supported_algorithms");
    if (!response.value("ok").toBool()) {
        return QJsonArray();
    }
    return response.value("result").toObject().value("algorithms").toArray();
}

WalletAccount WalletEngine::createAccount(const QString& label, int algId)
{
    if (!m_loaded) {
        m_lastError = "No wallet store is loaded.";
        emit error("No wallet store is loaded.");
        return WalletAccount();
    }
    if (m_locked) {
        m_lastError = "Wallet store is locked.";
        emit error("Wallet store is locked.");
        return WalletAccount();
    }
    QJsonObject args;
    args["label"] = label;
    args["algorithm"] = algorithmNameForId(algId);
    const QJsonObject response = backendResult("create_wallet", args, 120000);
    if (!response.value("ok").toBool()) {
        return WalletAccount();
    }
    reloadAccounts(true);
    const QJsonObject wallet = response.value("result").toObject().value("wallet").toObject();
    return walletFromJson(wallet);
}

WalletAccount WalletEngine::importAccount(const QJsonObject& json)
{
    Q_UNUSED(json);
    m_lastError = "Single-account import is not supported; import a canonical wallets.json file instead.";
    emit error("Single-account import is not supported; import a canonical wallets.json file instead.");
    return WalletAccount();
}

bool WalletEngine::importWalletsFile(const QString& sourcePath, bool merge)
{
    QJsonObject args;
    args["source_file"] = sourcePath;
    args["mode"] = merge ? "merge" : "replace";
    const QJsonObject response = backendResult("import_wallets", args, 120000);
    if (!response.value("ok").toBool()) {
        return false;
    }
    return reloadAccounts(true);
}

bool WalletEngine::removeAccount(const QString& accountId)
{
    if (m_locked) {
        m_lastError = "Wallet store is locked.";
        emit error("Wallet store is locked.");
        return false;
    }
    QJsonObject args;
    args["wallet_id"] = accountId;
    const QJsonObject response = backendResult("remove_wallet", args);
    if (!response.value("ok").toBool()) {
        return false;
    }
    return reloadAccounts(true);
}

bool WalletEngine::renameAccount(const QString& accountId, const QString& newLabel)
{
    if (m_locked) {
        m_lastError = "Wallet store is locked.";
        emit error("Wallet store is locked.");
        return false;
    }
    QJsonObject args;
    args["wallet_id"] = accountId;
    args["label"] = newLabel;
    const QJsonObject response = backendResult("rename_wallet", args);
    if (!response.value("ok").toBool()) {
        return false;
    }
    return reloadAccounts(true);
}

WalletAccount WalletEngine::setDefaultAccount(const QString& accountId)
{
    if (m_locked) {
        m_lastError = "Wallet store is locked.";
        emit error("Wallet store is locked.");
        return WalletAccount();
    }
    QJsonObject args;
    args["wallet_id"] = accountId;
    const QJsonObject response = backendResult("set_default", args);
    if (!response.value("ok").toBool()) {
        return WalletAccount();
    }
    reloadAccounts(true);
    return walletFromJson(response.value("result").toObject().value("wallet").toObject());
}

QList<WalletAccount> WalletEngine::listAccounts() const
{
    return m_accounts;
}

WalletAccount WalletEngine::getAccount(const QString& accountId) const
{
    for (const WalletAccount& account : m_accounts) {
        if (account.accountId == accountId) {
            return account;
        }
    }
    return WalletAccount();
}

QJsonObject WalletEngine::exportPublicInfo(const QString& accountId) const
{
    QJsonObject args;
    args["wallet_id"] = accountId;
    const QJsonObject response = backendResult("export_public", args);
    return response.value("ok").toBool()
        ? response.value("result").toObject().value("wallet").toObject()
        : QJsonObject();
}

bool WalletEngine::exportSecretMaterial(const QString& accountId, const QString& destination)
{
    QJsonObject args;
    args["wallet_id"] = accountId;
    args["destination"] = destination;
    const QJsonObject response = backendResult("export_secret", args);
    return response.value("ok").toBool();
}

bool WalletEngine::addContact(const QString& label, const QString& address, const QString& note)
{
    if (!validateAddress(address)) {
        m_lastError = "Invalid Animica address.";
        emit error("Invalid Animica address.");
        return false;
    }
    return m_addressBook->addContact(label, address, note);
}

bool WalletEngine::updateContact(const QString& address, const QString& label, const QString& note)
{
    if (!validateAddress(address)) {
        m_lastError = "Invalid Animica address.";
        emit error("Invalid Animica address.");
        return false;
    }
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

AddressBook::ImportResult WalletEngine::importContactsFile(const QString& sourcePath, bool replaceExisting)
{
    return m_addressBook->importFromFile(
        sourcePath,
        replaceExisting,
        [this](const QString& address) {
            return validateAddress(address);
        }
    );
}

AddressBook::ExportResult WalletEngine::exportContactsFile(const QString& destinationPath) const
{
    return m_addressBook->exportToFile(destinationPath);
}

bool WalletEngine::validateAddress(const QString& address) const
{
    QJsonObject args;
    args["address"] = address;
    const QJsonObject response = backendResult("validate_address", args);
    if (!response.value("ok").toBool()) {
        return false;
    }
    return response.value("result").toObject().value("valid").toBool();
}

QMap<QString, Balance> WalletEngine::getBalances() const
{
    return m_balanceTracker->getBalances();
}

Balance WalletEngine::getBalance(const QString& address) const
{
    return m_balanceTracker->getBalance(address);
}

void WalletEngine::startBalanceTracking()
{
    QStringList addresses;
    for (const WalletAccount& account : m_accounts) {
        if (!account.address.isEmpty()) {
            addresses.append(account.address);
        }
    }
    if (addresses.isEmpty()) {
        stopBalanceTracking();
        return;
    }
    m_balanceTracker->startTracking(addresses);
}

void WalletEngine::stopBalanceTracking()
{
    m_balanceTracker->stopTracking();
}

void WalletEngine::refreshBalances()
{
    if (!m_locked) {
        startBalanceTracking();
        m_balanceTracker->refresh();
    }
}

QString WalletEngine::signTransaction(const QJsonObject& txJson, const QString& fromAccountId)
{
    Q_UNUSED(txJson);
    Q_UNUSED(fromAccountId);
    m_lastError = "Direct transaction signing is not exposed; use the canonical send flow.";
    emit error("Direct transaction signing is not exposed; use the canonical send flow.");
    return QString();
}

QJsonObject WalletEngine::submitTransaction(const QJsonObject& request)
{
    const QJsonObject response = backendResult("send_transaction", request, 180000);
    return response.value("ok").toBool() ? response.value("result").toObject() : QJsonObject();
}

// ==================== ANM Instant (L2) ====================

QJsonObject WalletEngine::l2Status() const
{
    if (!m_rpcClient) {
        return QJsonObject();
    }
    return m_rpcClient->l2StatusJson();
}

QJsonObject WalletEngine::l2Balance(const QString& address) const
{
    if (!m_rpcClient || address.trimmed().isEmpty()) {
        return QJsonObject();
    }
    return m_rpcClient->l2GetBalanceJson(address);
}

QJsonObject WalletEngine::l2TransactionStatus(const QString& txid) const
{
    if (!m_rpcClient || txid.trimmed().isEmpty()) {
        return QJsonObject();
    }
    return m_rpcClient->l2GetTransactionJson(txid);
}

QJsonObject WalletEngine::sendInstant(const QString& fromAddress, const QString& toAddress, const QString& amount)
{
    if (m_locked) {
        m_lastError = "Wallet is locked.";
        emit error(m_lastError);
        return QJsonObject();
    }
    QJsonObject request;
    request["from_address"] = fromAddress;
    request["to_address"] = toAddress;
    request["amount"] = amount;
    // The bridge performs l2_prepareTransfer -> ml_dsa_65.sign(signingHash) ->
    // l2_submitSigned with the sender account's existing ML-DSA-65 key.
    const QJsonObject response = backendResult("l2_send_instant", request, 180000);
    return response.value("ok").toBool() ? response.value("result").toObject() : QJsonObject();
}

QJsonObject WalletEngine::transactionStatus(const QString& txHash) const
{
    QJsonObject args;
    args["tx_hash"] = txHash;
    const QJsonObject response = backendResult("transaction_status", args, 30000);
    return response.value("ok").toBool() ? response.value("result").toObject() : QJsonObject();
}

QJsonObject WalletEngine::transactionDetails(const QString& txHash) const
{
    QJsonObject args;
    args["tx_hash"] = txHash;
    const QJsonObject response = backendResult("transaction_details", args, 30000);
    return response.value("ok").toBool() ? response.value("result").toObject() : QJsonObject();
}

QJsonObject WalletEngine::fetchTransactionHistory(const QJsonObject& filters) const
{
    const QJsonObject response = backendResult("fetch_history", filters, 60000);
    return response.value("ok").toBool() ? response.value("result").toObject() : QJsonObject();
}

QJsonObject WalletEngine::contractRead(const QJsonObject& request) const
{
    const QJsonObject response = backendResult("contract_read", request, 60000);
    return response.value("ok").toBool() ? response.value("result").toObject() : QJsonObject();
}

QJsonObject WalletEngine::rawContractRead(const QJsonObject& request) const
{
    const QJsonObject response = backendResult("raw_contract_read", request, 60000);
    return response.value("ok").toBool() ? response.value("result").toObject() : QJsonObject();
}

QJsonObject WalletEngine::previewContractCall(const QJsonObject& request) const
{
    const QJsonObject response = backendResult("preview_contract_call", request, 30000);
    return response.value("ok").toBool() ? response.value("result").toObject() : QJsonObject();
}

QJsonObject WalletEngine::contractWrite(const QJsonObject& request)
{
    const QJsonObject response = backendResult("contract_write", request, 180000);
    return response.value("ok").toBool() ? response.value("result").toObject() : QJsonObject();
}

void WalletEngine::setRpcEndpoint(const QString& rpcUrl)
{
    if (m_rpcClient && m_rpcClient->endpoint() != rpcUrl) {
        m_rpcClient->setEndpoint(rpcUrl);
    }
    m_backend->setRpcUrl(rpcUrl);
}

void WalletEngine::setExplorerUrl(const QString& explorerUrl)
{
    m_backend->setExplorerUrl(explorerUrl);
}
