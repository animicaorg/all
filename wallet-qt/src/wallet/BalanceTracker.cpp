#include "BalanceTracker.h"
#include "../rpc/AnimicaRpcClient.h"
#include "../rpc/RpcReply.h"
#include <QJsonDocument>
#include <QJsonObject>
#include <QDebug>

BalanceTracker::BalanceTracker(AnimicaRpcClient* rpcClient, QObject* parent)
    : QObject(parent)
    , m_rpcClient(rpcClient)
    , m_tracking(false)
    , m_syncing(false)
{
    connect(&m_pollTimer, &QTimer::timeout, this, &BalanceTracker::pollBalances);
    m_pollTimer.setInterval(5000);
}

void BalanceTracker::startTracking(const QStringList& addresses)
{
    m_addresses = addresses;
    m_tracking = true;
    
    // Initialize balance entries
    for (const QString& address : addresses) {
        if (!m_balances.contains(address)) {
            Balance balance;
            balance.address = address;
            m_balances[address] = balance;
        }
    }
    
    // Immediate fetch
    pollBalances();
    
    // Start polling
    m_pollTimer.start();
}

void BalanceTracker::stopTracking()
{
    m_tracking = false;
    m_pollTimer.stop();
}

QMap<QString, Balance> BalanceTracker::getBalances() const
{
    return m_balances;
}

Balance BalanceTracker::getBalance(const QString& address) const
{
    return m_balances.value(address, Balance());
}

void BalanceTracker::refresh()
{
    if (m_tracking) {
        pollBalances();
    }
}

void BalanceTracker::setPollingInterval(int intervalMs)
{
    m_pollTimer.setInterval(intervalMs);
}

void BalanceTracker::pollBalances()
{
    if (!m_rpcClient) {
        return;
    }
    
    // Fetch sync status
    fetchSyncStatus();
    
    // Fetch balances for all tracked addresses
    for (const QString& address : m_addresses) {
        fetchBalance(address);
    }
}

void BalanceTracker::fetchBalance(const QString& address)
{
    RpcReply* reply = m_rpcClient->getBalance(address, "latest");
    if (!reply) {
        return;
    }
    
    // Store address in reply property for later retrieval
    reply->setProperty("address", address);
    
    connect(reply, &RpcReply::finished, this, &BalanceTracker::handleBalanceReply);
}

void BalanceTracker::fetchSyncStatus()
{
    RpcReply* reply = m_rpcClient->getSyncStatus();
    if (!reply) {
        return;
    }
    
    connect(reply, &RpcReply::finished, this, &BalanceTracker::handleSyncStatusReply);
}

void BalanceTracker::handleBalanceReply()
{
    RpcReply* reply = qobject_cast<RpcReply*>(sender());
    if (!reply) {
        return;
    }
    
    QString address = reply->property("address").toString();
    
    if (reply->error() != QNetworkReply::NoError) {
        qWarning() << "Balance fetch error for" << address << ":" << reply->errorString();
        emit error(QString("Failed to fetch balance: %1").arg(reply->errorString()));
        reply->deleteLater();
        return;
    }
    
    QByteArray data = reply->readAll();
    QJsonDocument doc = QJsonDocument::fromJson(data);
    
    if (!doc.isObject()) {
        qWarning() << "Invalid balance response for" << address;
        reply->deleteLater();
        return;
    }
    
    QJsonObject obj = doc.object();
    
    // Check for JSON-RPC error
    if (obj.contains("error")) {
        QJsonObject error = obj["error"].toObject();
        qWarning() << "RPC error:" << error["message"].toString();
        emit this->error(error["message"].toString());
        reply->deleteLater();
        return;
    }
    
    quint64 balance = 0;
    bool ok = false;
    const QJsonValue result = obj["result"];
    if (result.isString()) {
        QString balanceText = result.toString().trimmed();
        int base = 10;
        if (balanceText.startsWith("0x")) {
            balanceText = balanceText.mid(2);
            base = 16;
        }
        balance = balanceText.toULongLong(&ok, base);
    } else if (result.isDouble()) {
        balance = static_cast<quint64>(result.toDouble());
        ok = true;
    }
    
    if (ok) {
        Balance& balanceData = m_balances[address];
        bool changed = (balanceData.confirmed != balance);
        
        balanceData.confirmed = balance;
        balanceData.syncing = m_syncing;
        
        if (changed) {
            emit balanceUpdated(address, balanceData);
        }
    }
    
    reply->deleteLater();
}

void BalanceTracker::handleSyncStatusReply()
{
    RpcReply* reply = qobject_cast<RpcReply*>(sender());
    if (!reply) {
        return;
    }
    
    if (reply->error() != QNetworkReply::NoError) {
        qWarning() << "Sync status fetch error:" << reply->errorString();
        reply->deleteLater();
        return;
    }
    
    QByteArray data = reply->readAll();
    QJsonDocument doc = QJsonDocument::fromJson(data);
    
    if (!doc.isObject()) {
        reply->deleteLater();
        return;
    }
    
    QJsonObject obj = doc.object();
    QJsonValue result = obj["result"];
    
    bool syncing = false;
    if (result.isBool()) {
        syncing = result.toBool();
    } else if (result.isObject()) {
        syncing = true;
    }
    
    if (m_syncing != syncing) {
        m_syncing = syncing;
        emit syncStatusChanged(syncing);
        
        // Update sync status for all balances
        for (auto& balance : m_balances) {
            balance.syncing = syncing;
        }
    }
    
    reply->deleteLater();
}
