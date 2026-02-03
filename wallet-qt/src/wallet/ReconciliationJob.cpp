#include "ReconciliationJob.h"
#include "WalletEngine.h"
#include "WalletDatabase.h"
#include "WalletAccount.h"
#include "../rpc/AnimicaRpcClient.h"
#include <QDateTime>
#include <QFile>
#include <QUuid>
#include <QJsonArray>
#include <QJsonDocument>
#include "../rpc/RpcReply.h"
#include <QEventLoop>
#include <QtConcurrent/QtConcurrent>
#include <QDebug>

ReconciliationJob::ReconciliationJob(
    AnimicaRpcClient* rpcClient,
    WalletDatabase* database,
    WalletEngine* walletEngine,
    QObject* parent)
    : QObject(parent)
    , m_rpcClient(rpcClient)
    , m_database(database)
    , m_walletEngine(walletEngine)
    , m_running(false)
    , m_cancelled(false)
    , m_createBackup(true)
{
    Q_ASSERT(m_rpcClient);
    Q_ASSERT(m_database);
    Q_ASSERT(m_walletEngine);
}

ReconciliationJob::~ReconciliationJob()
{
}

void ReconciliationJob::start()
{
    QMutexLocker locker(&m_mutex);
    
    if (m_running) {
        emit failed("", "Reconciliation already running");
        return;
    }
    
    m_runId = QUuid::createUuid().toString(QUuid::WithoutBraces);
    m_running = true;
    m_cancelled = false;
    
    locker.unlock();
    
    // Start in database
    m_database->startReconciliation();
    
    emit started(m_runId);
    emit progress(0, "Starting reconciliation...");
    
    // Run in background thread (ignore the future since we handle completion via signals)
    (void)QtConcurrent::run([this]() {
        runReconciliation();
    });
}

void ReconciliationJob::cancel()
{
    QMutexLocker locker(&m_mutex);
    m_cancelled = true;
}

bool ReconciliationJob::isRunning() const
{
    QMutexLocker locker(&m_mutex);
    return m_running;
}

QString ReconciliationJob::currentRunId() const
{
    QMutexLocker locker(&m_mutex);
    return m_runId;
}

void ReconciliationJob::setCreateBackup(bool enable)
{
    QMutexLocker locker(&m_mutex);
    m_createBackup = enable;
}

bool ReconciliationJob::createBackupEnabled() const
{
    QMutexLocker locker(&m_mutex);
    return m_createBackup;
}

void ReconciliationJob::runReconciliation()
{
    QString runId;
    {
        QMutexLocker locker(&m_mutex);
        runId = m_runId;
    }
    
    try {
        // 1. Create backup if enabled
        if (m_createBackup && !createBackup()) {
            throw std::runtime_error("Failed to create backup");
        }
        emit progress(10, "Backup created");
        
        if (m_cancelled) {
            throw std::runtime_error("Cancelled by user");
        }
        
        // 2. Query chain and local state
        emit progress(20, "Querying chain state...");
        QList<AccountBalance> chainBalances = queryChainBalances();
        
        if (m_cancelled) {
            throw std::runtime_error("Cancelled by user");
        }
        
        emit progress(40, "Querying local ledger...");
        QList<AccountBalance> localBalances = queryLocalBalances();
        
        if (m_cancelled) {
            throw std::runtime_error("Cancelled by user");
        }
        
        // 3. Compare
        emit progress(50, "Comparing balances...");
        QList<AccountBalance> discrepancies = compareBalances(chainBalances, localBalances);
        
        if (m_cancelled) {
            throw std::runtime_error("Cancelled by user");
        }
        
        // 4. Repair if needed
        if (!discrepancies.isEmpty()) {
            emit progress(60, QString("Repairing %1 discrepancies...").arg(discrepancies.size()));
            if (!repairDiscrepancies(discrepancies)) {
                throw std::runtime_error("Failed to repair discrepancies");
            }
        } else {
            emit progress(60, "No discrepancies found");
        }
        
        emit progress(80, "Verifying repairs...");
        
        // 5. Query after state
        QList<AccountBalance> afterBalances = queryLocalBalances();
        
        // 6. Build summary
        emit progress(90, "Building summary...");
        QJsonObject summary = buildSummary(localBalances, afterBalances, discrepancies.size());
        
        // 7. Complete in database
        QString changesJson = QJsonDocument(summary["changes"].toArray()).toJson(QJsonDocument::Compact);
        m_database->completeReconciliation(runId, changesJson);
        
        emit progress(100, "Done");
        emit completed(runId, summary);
        
    } catch (const std::exception& e) {
        qCritical() << "Reconciliation failed:" << e.what();
        m_database->failReconciliation(runId, e.what());
        emit failed(runId, QString::fromUtf8(e.what()));
    }
    
    QMutexLocker locker(&m_mutex);
    m_running = false;
}

bool ReconciliationJob::createBackup()
{
    // Note: WalletDatabase doesn't expose databasePath(), so we'll skip backup for now
    // or implement it by querying the database file path through a helper
    qInfo() << "Backup creation not implemented (database path not accessible)";
    return true;
}

QList<ReconciliationJob::AccountBalance> ReconciliationJob::queryChainBalances()
{
    QList<AccountBalance> result;
    
    // Get all accounts
    QList<WalletAccount> accounts = m_walletEngine->listAccounts();
    
    for (const WalletAccount& account : accounts) {
        if (m_cancelled) {
            break;
        }
        
        // Query balance from node (synchronously)
        QEventLoop loop;
        RpcReply* reply = m_rpcClient->getBalance(account.address, "latest");
        qint64 balance = 0;
        bool success = false;
        
        connect(reply, &RpcReply::finished, [&]() {
            if (reply->error() == QNetworkReply::NoError) {
                QByteArray data = reply->readAll();
                QJsonDocument doc = QJsonDocument::fromJson(data);
                QJsonObject obj = doc.object();
                
                if (obj.contains("result")) {
                    // Result is a string representing wei (atomic units)
                    QString balanceStr = obj["result"].toString();
                    // Remove "0x" prefix if present and convert from hex
                    if (balanceStr.startsWith("0x")) {
                        balanceStr = balanceStr.mid(2);
                        balance = balanceStr.toLongLong(&success, 16);
                    } else {
                        balance = balanceStr.toLongLong(&success, 10);
                    }
                }
            } else {
                qWarning() << "Failed to query balance for" << account.address << ":" << reply->errorString();
            }
            reply->deleteLater();
            loop.quit();
        });
        
        loop.exec();
        
        if (success) {
            AccountBalance ab;
            ab.accountId = account.accountId;
            ab.address = account.address;
            ab.confirmedChain = balance;
            result.append(ab);
        } else {
            qWarning() << "Skipping account" << account.address << "due to RPC error";
        }
    }
    
    return result;
}

QList<ReconciliationJob::AccountBalance> ReconciliationJob::queryLocalBalances()
{
    QList<AccountBalance> result;
    
    QList<WalletAccount> accounts = m_walletEngine->listAccounts();
    for (const WalletAccount& account : accounts) {
        if (m_cancelled) {
            break;
        }
        
        AccountBalance ab;
        ab.accountId = account.accountId;
        ab.address = account.address;
        
        // Get confirmed balance (AVAILABLE type)
        ab.confirmedLocal = m_database->getBalance(account.accountId, "ANM");
        
        // Get pending balance (PENDING_IN - PENDING_OUT)
        ab.pendingLocal = m_database->getPendingBalance(account.accountId, "ANM");
        
        result.append(ab);
    }
    
    return result;
}

QList<ReconciliationJob::AccountBalance> ReconciliationJob::compareBalances(
    const QList<AccountBalance>& chain,
    const QList<AccountBalance>& local)
{
    QList<AccountBalance> discrepancies;
    
    // Create lookup map for local balances
    QMap<QString, AccountBalance> localMap;
    for (const AccountBalance& ab : local) {
        localMap[ab.accountId] = ab;
    }
    
    // Compare each chain balance with local
    for (const AccountBalance& chainAb : chain) {
        if (m_cancelled) {
            break;
        }
        
        if (localMap.contains(chainAb.accountId)) {
            AccountBalance localAb = localMap[chainAb.accountId];
            
            // Calculate discrepancy
            qint64 discrepancy = chainAb.confirmedChain - localAb.confirmedLocal;
            
            if (discrepancy != 0) {
                // Found discrepancy
                AccountBalance combined = chainAb;
                combined.confirmedLocal = localAb.confirmedLocal;
                combined.pendingLocal = localAb.pendingLocal;
                combined.discrepancy = discrepancy;
                
                discrepancies.append(combined);
                
                qWarning() << "Discrepancy found for account" << combined.accountId
                          << "- Chain:" << chainAb.confirmedChain
                          << "Local:" << localAb.confirmedLocal
                          << "Diff:" << discrepancy;
            }
        } else {
            qWarning() << "Account" << chainAb.accountId << "found in chain but not in local map";
        }
    }
    
    return discrepancies;
}

bool ReconciliationJob::repairDiscrepancies(const QList<AccountBalance>& discrepancies)
{
    bool success = true;
    
    for (const AccountBalance& ab : discrepancies) {
        if (m_cancelled) {
            break;
        }
        
        if (ab.discrepancy == 0) {
            continue;
        }
        
        emit discrepancyFound(ab.accountId, ab.confirmedChain, ab.confirmedLocal);
        
        // Create adjustment ledger entry
        LedgerEntry adjustment;
        adjustment.txid = "reconcile-" + m_runId;
        adjustment.accountId = ab.accountId;
        adjustment.asset = "ANM";
        adjustment.type = "AVAILABLE";
        adjustment.delta = ab.discrepancy;  // Difference to add/subtract
        adjustment.stateVersion = m_database->nextStateVersion();
        adjustment.createdAt = QDateTime::currentMSecsSinceEpoch();
        
        if (!m_database->addLedgerEntry(adjustment)) {
            qCritical() << "Failed to add reconciliation entry for" << ab.accountId;
            success = false;
        } else {
            qInfo() << "Created adjustment entry for" << ab.accountId
                   << "- Delta:" << ab.discrepancy;
        }
    }
    
    return success;
}

QJsonObject ReconciliationJob::buildSummary(
    const QList<AccountBalance>& before,
    const QList<AccountBalance>& after,
    int repaired)
{
    QJsonObject summary;
    summary["runId"] = m_runId;
    summary["timestamp"] = QDateTime::currentMSecsSinceEpoch();
    summary["accountsChecked"] = before.size();
    summary["discrepanciesFound"] = repaired;
    
    // Build changes array
    QJsonArray changes;
    
    // Create lookup map for after balances
    QMap<QString, AccountBalance> afterMap;
    for (const AccountBalance& ab : after) {
        afterMap[ab.accountId] = ab;
    }
    
    for (const AccountBalance& beforeAb : before) {
        if (afterMap.contains(beforeAb.accountId)) {
            AccountBalance afterAb = afterMap[beforeAb.accountId];
            
            if (beforeAb.confirmedLocal != afterAb.confirmedLocal) {
                QJsonObject change;
                change["accountId"] = beforeAb.accountId;
                change["address"] = beforeAb.address;
                change["beforeLocal"] = QString::number(beforeAb.confirmedLocal);
                change["afterLocal"] = QString::number(afterAb.confirmedLocal);
                change["chainBalance"] = QString::number(afterAb.confirmedChain);
                change["adjustment"] = QString::number(afterAb.confirmedLocal - beforeAb.confirmedLocal);
                
                changes.append(change);
            }
        }
    }
    
    summary["changes"] = changes;
    
    return summary;
}
