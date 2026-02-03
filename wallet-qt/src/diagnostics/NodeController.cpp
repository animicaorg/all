#include "NodeController.h"
#include "../rpc/AnimicaRpcClient.h"
#include <QJsonObject>
#include <QJsonArray>
#include <QJsonDocument>
#include <QEventLoop>
#include <QTimer>
#include "../rpc/RpcReply.h"
#include <QProcessEnvironment>
#include <QSysInfo>

NodeController::NodeController(AnimicaRpcClient* rpcClient, QObject* parent)
    : QObject(parent)
    , m_rpcClient(rpcClient)
{
}

NodeController::NodeStatus NodeController::queryStatus()
{
    NodeStatus status;
    status.available = false;
    status.fetchTime = QDateTime::currentDateTime();

    if (!m_rpcClient) {
        return status;
    }

    // Try to call node.getStatus
    QString response = executeRpcSync("node.getStatus");
    if (response.isEmpty()) {
        return status;
    }

    // Parse JSON response
    QJsonDocument doc = QJsonDocument::fromJson(response.toUtf8());
    if (doc.isNull() || !doc.isObject()) {
        return status;
    }

    QJsonObject root = doc.object();
    if (root.contains("result")) {
        status = parseNodeStatus(root.value("result").toObject());
        status.available = true;
    }

    return status;
}

QString NodeController::triggerBootstrap(const QString& operatorName)
{
    QString result = executeRpcSync("node.bootstrap");
    logAction("Bootstrap", getOperatorName(operatorName), result);
    return result;
}

QString NodeController::forceSyncRound(const QString& operatorName)
{
    QString result = executeRpcSync("sync.force");
    logAction("Force Sync", getOperatorName(operatorName), result);
    return result;
}

QString NodeController::pauseSync(const QString& operatorName)
{
    QString result = executeRpcSync("sync.pause");
    logAction("Pause Sync", getOperatorName(operatorName), result);
    return result;
}

QString NodeController::resumeSync(const QString& operatorName)
{
    QString result = executeRpcSync("sync.resume");
    logAction("Resume Sync", getOperatorName(operatorName), result);
    return result;
}

NodeController::NodeStatus NodeController::parseNodeStatus(const QJsonObject& json)
{
    NodeStatus status;

    // Parse chain status
    if (json.contains("chain")) {
        QJsonObject chain = json.value("chain").toObject();
        status.chain.chainId = chain.value("chainId").toInt();
        
        QJsonObject head = chain.value("head").toObject();
        status.chain.headHeight = head.value("height").toVariant().toLongLong();
        status.chain.headHash = head.value("hash").toString();
        status.chain.headTimestamp = head.value("timestamp").toVariant().toLongLong();
        
        QJsonObject bestHeader = chain.value("bestHeader").toObject();
        status.chain.bestHeaderHeight = bestHeader.value("height").toVariant().toLongLong();
        status.chain.bestHeaderHash = bestHeader.value("hash").toString();
    }

    // Parse sync status
    if (json.contains("sync")) {
        QJsonObject sync = json.value("sync").toObject();
        status.sync.phase = sync.value("phase").toString();
        status.sync.progress = sync.value("progress").toDouble();
        status.sync.currentHeight = sync.value("currentHeight").toVariant().toLongLong();
        status.sync.targetHeight = sync.value("targetHeight").toVariant().toLongLong();
        status.sync.inFlightHeaders = sync.value("inFlightHeaders").toInt();
        status.sync.queueDepth = sync.value("queueDepth").toInt();
    }

    // Parse P2P status
    if (json.contains("p2p")) {
        QJsonObject p2p = json.value("p2p").toObject();
        status.peers.inbound = p2p.value("peersInbound").toInt();
        status.peers.outbound = p2p.value("peersOutbound").toInt();
        status.peers.total = p2p.value("totalPeers").toInt();
        
        QJsonArray listenAddrs = p2p.value("listenAddrs").toArray();
        for (const QJsonValue& addr : listenAddrs) {
            status.peers.listenAddrs.append(addr.toString());
        }
    }

    // Parse mempool status
    if (json.contains("mempool")) {
        QJsonObject mempool = json.value("mempool").toObject();
        status.mempool.txCount = mempool.value("txCount").toInt();
        status.mempool.rejectedLast1h = mempool.value("rejectedLast1h").toInt();
    }

    // Parse hashrate status
    if (json.contains("hashrate")) {
        QJsonObject hashrate = json.value("hashrate").toObject();
        status.hashrate.hashrateSps = hashrate.value("hashrateHsps").toDouble();
        // Fallback for old naming
        if (status.hashrate.hashrateSps == 0.0) {
            status.hashrate.hashrateSps = hashrate.value("hashrate_hsps").toDouble();
        }
        status.hashrate.windowBlocks = hashrate.value("window_blocks").toInt();
    }

    return status;
}

QString NodeController::executeRpcSync(const QString& method, const QJsonValue& params)
{
    if (!m_rpcClient) {
        return QString();
    }

    RpcReply* reply = m_rpcClient->call(method, params);
    if (!reply) {
        return QString();
    }

    // Wait for response with 30 second timeout
    QEventLoop loop;
    QTimer timeout;
    timeout.setSingleShot(true);
    timeout.setInterval(30000);

    connect(reply, &RpcReply::finished, &loop, &QEventLoop::quit);
    connect(&timeout, &QTimer::timeout, &loop, &QEventLoop::quit);

    timeout.start();
    loop.exec();

    if (!timeout.isActive()) {
        // Timeout
        reply->deleteLater();
        return QString();
    }

    timeout.stop();

    // Check for errors
    if (reply->error() != QNetworkReply::NoError) {
        reply->deleteLater();
        return QString();
    }

    // Read response
    QByteArray data = reply->readAll();
    reply->deleteLater();

    return QString::fromUtf8(data);
}

void NodeController::logAction(const QString& action, const QString& operatorName, const QString& result)
{
    emit actionLogged(QDateTime::currentDateTime(), action, operatorName, result);
}

QString NodeController::getOperatorName(const QString& providedName)
{
    if (!providedName.isEmpty()) {
        return providedName;
    }
    
    // Try to get system username
    QProcessEnvironment env = QProcessEnvironment::systemEnvironment();
    QString username = env.value("USER");  // Unix/Linux/macOS
    if (username.isEmpty()) {
        username = env.value("USERNAME");  // Windows
    }
    if (username.isEmpty()) {
        username = "Anonymous";
    }
    
    return username + "@" + QSysInfo::machineHostName();
}
