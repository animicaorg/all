#include "AnimicaRpcClient.h"
#include <QNetworkRequest>
#include <QJsonDocument>
#include <QJsonArray>
#include <QEventLoop>
#include <QTimer>
#include <QDebug>

AnimicaRpcClient::AnimicaRpcClient(QObject* parent)
    : QObject(parent)
    , m_network(new QNetworkAccessManager(this))
    , m_timeout(30000)
    , m_requestId(1)
{
    // Network manager is reused for connection pooling
}

AnimicaRpcClient::~AnimicaRpcClient()
{
    // QObject parent-child relationship handles cleanup
}

void AnimicaRpcClient::setEndpoint(const QString& url)
{
    m_endpoint = QUrl(url);
    qDebug() << "RPC endpoint set to:" << m_endpoint.toString();
}

// ==================== Health & System ====================

QNetworkReply* AnimicaRpcClient::ping()
{
    return call("node.ping", QJsonArray());
}

// ==================== Chain Information ====================

QNetworkReply* AnimicaRpcClient::getChainId()
{
    return call("chain.getChainId", QJsonArray());
}

QNetworkReply* AnimicaRpcClient::getHead()
{
    return call("chain.getHead", QJsonArray());
}

QNetworkReply* AnimicaRpcClient::getBlockByNumber(const QString& number, bool fullTx)
{
    QJsonArray params;
    params.append(number);
    params.append(fullTx);
    return call("chain.getBlockByNumber", params);
}

QNetworkReply* AnimicaRpcClient::getBlockByHash(const QString& hash, bool fullTx)
{
    QJsonArray params;
    params.append(hash);
    params.append(fullTx);
    return call("chain.getBlockByHash", params);
}

// ==================== Sync Status ====================

QNetworkReply* AnimicaRpcClient::getSyncStatus()
{
    return call("sync.getStatus", QJsonArray());
}

// ==================== State Queries ====================

QNetworkReply* AnimicaRpcClient::getBalance(const QString& address, const QString& block)
{
    QJsonArray params;
    params.append(address);
    params.append(block);
    return call("state.getBalance", params);
}

QNetworkReply* AnimicaRpcClient::getNonce(const QString& address, const QString& block)
{
    QJsonArray params;
    params.append(address);
    params.append(block);
    return call("state.getNonce", params);
}

// ==================== Transactions ====================

QNetworkReply* AnimicaRpcClient::sendRawTransaction(const QString& signedTx)
{
    QJsonArray params;
    params.append(signedTx);
    return call("tx.sendRawTransaction", params);
}

QNetworkReply* AnimicaRpcClient::getTransaction(const QString& hash)
{
    QJsonArray params;
    params.append(hash);
    return call("tx.getTransactionByHash", params);
}

QNetworkReply* AnimicaRpcClient::getReceipt(const QString& hash)
{
    QJsonArray params;
    params.append(hash);
    return call("tx.getTransactionReceipt", params);
}

// ==================== P2P Network ====================

QNetworkReply* AnimicaRpcClient::listPeers()
{
    return call("p2p.listPeers", QJsonArray());
}

QNetworkReply* AnimicaRpcClient::getPeerCount()
{
    // Try multiple possible method names
    return call("p2p.peerCount", QJsonArray());
}

QNetworkReply* AnimicaRpcClient::getChainParams()
{
    return call("chain.getParams", QJsonArray());
}

// ==================== Private Methods ====================

QNetworkReply* AnimicaRpcClient::call(const QString& method)
{
    // No-parameter overload: use empty array as params
    return call(method, QJsonArray());
}

QNetworkReply* AnimicaRpcClient::call(const QString& method, const QJsonValue& params)
{
    QJsonObject request = buildRequest(method, params);
    QJsonDocument doc(request);
    QByteArray data = doc.toJson(QJsonDocument::Compact);

    QNetworkRequest netRequest(m_endpoint);
    netRequest.setHeader(QNetworkRequest::ContentTypeHeader, "application/json");
    
    // Set timeout (requires Qt 5.15+)
    #if QT_VERSION >= QT_VERSION_CHECK(5, 15, 0)
    netRequest.setTransferTimeout(m_timeout);
    #endif

    qDebug() << "RPC request:" << method << "to" << m_endpoint.toString();
    
    QNetworkReply* reply = m_network->post(netRequest, data);
    
    // Log errors
    connect(reply, &QNetworkReply::errorOccurred, this, [this, method, reply]() {
        QString errorMsg = QString("RPC error for %1: %2").arg(method, reply->errorString());
        qWarning() << errorMsg;
        emit error(errorMsg);
    });
    
    return reply;
}

// ==================== Synchronous JSON Wrappers ====================

QJsonValue AnimicaRpcClient::rpcCallSync(const QString& method, const QJsonValue& params)
{
    QJsonObject request = buildRequest(method, params);
    QJsonDocument doc(request);
    QByteArray data = doc.toJson(QJsonDocument::Compact);

    QNetworkRequest netRequest(m_endpoint);
    netRequest.setHeader(QNetworkRequest::ContentTypeHeader, "application/json");
    
    // Set timeout
    #if QT_VERSION >= QT_VERSION_CHECK(5, 15, 0)
    netRequest.setTransferTimeout(m_timeout);
    #endif

    QNetworkReply* reply = m_network->post(netRequest, data);
    
    // Block with event loop
    QEventLoop loop;
    QTimer timeoutTimer;
    timeoutTimer.setSingleShot(true);
    
    connect(reply, &QNetworkReply::finished, &loop, &QEventLoop::quit);
    connect(&timeoutTimer, &QTimer::timeout, &loop, &QEventLoop::quit);
    
    timeoutTimer.start(m_timeout);
    loop.exec();
    
    // Check for timeout
    if (!timeoutTimer.isActive()) {
        qWarning() << "RPC call timed out:" << method;
        reply->abort();
        reply->deleteLater();
        return QJsonValue();
    }
    
    timeoutTimer.stop();
    
    // Check for network error
    if (reply->error() != QNetworkReply::NoError) {
        QString errorMsg = QString("RPC error for %1: %2").arg(method, reply->errorString());
        qWarning() << errorMsg;
        reply->deleteLater();
        return QJsonValue();
    }
    
    // Parse response
    QByteArray responseData = reply->readAll();
    reply->deleteLater();
    
    QJsonDocument responseDoc = QJsonDocument::fromJson(responseData);
    if (!responseDoc.isObject()) {
        qWarning() << "Invalid JSON-RPC response for" << method;
        return QJsonValue();
    }
    
    QJsonObject responseObj = responseDoc.object();
    
    // Check for JSON-RPC error
    if (responseObj.contains("error")) {
        QJsonObject errorObj = responseObj["error"].toObject();
        QString errorMsg = QString("RPC error %1: %2")
            .arg(errorObj["code"].toInt())
            .arg(errorObj["message"].toString());
        qWarning() << errorMsg;
        return QJsonValue();
    }
    
    // Return result
    return responseObj["result"];
}

QJsonObject AnimicaRpcClient::getHeadJson()
{
    QJsonValue result = rpcCallSync("chain.getHead", QJsonArray());
    if (result.isObject()) {
        return result.toObject();
    }
    return QJsonObject();
}

QJsonObject AnimicaRpcClient::getBlockByNumberJson(qint64 number, bool fullTx)
{
    QJsonArray params;
    params.append(QString::number(number));
    params.append(fullTx);
    
    QJsonValue result = rpcCallSync("chain.getBlockByNumber", params);
    if (result.isObject()) {
        return result.toObject();
    }
    return QJsonObject();
}

QJsonObject AnimicaRpcClient::getTransactionByHash(const QString& txHash)
{
    QJsonArray params;
    params.append(txHash);
    
    QJsonValue result = rpcCallSync("tx.getTransactionByHash", params);
    if (result.isObject()) {
        return result.toObject();
    }
    return QJsonObject();
}

QJsonObject AnimicaRpcClient::buildRequest(const QString& method, const QJsonValue& params)
{
    QJsonObject request;
    request["jsonrpc"] = "2.0";
    request["method"] = method;
    request["params"] = params;
    request["id"] = nextId();
    return request;
}

int AnimicaRpcClient::nextId()
{
    return m_requestId++;
}
