#include "AnimicaRpcClient.h"
#include <QNetworkRequest>
#include <QJsonDocument>
#include <QJsonArray>
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
