#include "AnimicaRpcClient.h"
#include "RpcReply.h"
#include <QNetworkRequest>
#include <QJsonDocument>
#include <QJsonArray>
#include <QEventLoop>
#include <QTimer>
#include <QDebug>
#include <QtGlobal>

AnimicaRpcClient::AnimicaRpcClient(QObject* parent)
    : QObject(parent)
    , m_network(new QNetworkAccessManager(this))
    , m_timeout(8000)
    , m_maxRetries(2)
    , m_backoffMs(500)
    , m_requestId(1)
    , m_connected(false)
{
    // Network manager is reused for connection pooling
}

AnimicaRpcClient::~AnimicaRpcClient()
{
    // QObject parent-child relationship handles cleanup
}

void AnimicaRpcClient::setEndpoint(const QString& url)
{
    QUrl parsed(url);
    m_username = parsed.userName();
    m_password = parsed.password();
    parsed.setUserName(QString());
    parsed.setPassword(QString());
    m_endpoint = parsed;
    qDebug() << "RPC endpoint set to:" << m_endpoint.toString();
}

void AnimicaRpcClient::setRetryPolicy(int maxRetries, int backoffMs)
{
    m_maxRetries = qMax(0, maxRetries);
    m_backoffMs = qMax(0, backoffMs);
}

// ==================== Health & System ====================

RpcReply* AnimicaRpcClient::ping()
{
    return call("node.ping", QJsonArray());
}

// ==================== Chain Information ====================

RpcReply* AnimicaRpcClient::getChainId()
{
    return call("chain.getChainId", QJsonArray());
}

RpcReply* AnimicaRpcClient::getHead()
{
    return call("chain.getHead", QJsonArray());
}

RpcReply* AnimicaRpcClient::getBlockByNumber(const QString& number, bool fullTx)
{
    QJsonArray params;
    params.append(number);
    params.append(fullTx);
    return call("chain.getBlockByNumber", params);
}

RpcReply* AnimicaRpcClient::getBlockByHash(const QString& hash, bool fullTx)
{
    QJsonArray params;
    params.append(hash);
    params.append(fullTx);
    return call("chain.getBlockByHash", params);
}

// ==================== Sync Status ====================

RpcReply* AnimicaRpcClient::getSyncStatus()
{
    return call("sync.getStatus", QJsonArray());
}

// ==================== State Queries ====================

RpcReply* AnimicaRpcClient::getBalance(const QString& address, const QString& block)
{
    QJsonArray params;
    params.append(address);
    params.append(block);
    return call("state.getBalance", params);
}

RpcReply* AnimicaRpcClient::getNonce(const QString& address, const QString& block)
{
    QJsonArray params;
    params.append(address);
    params.append(block);
    return call("state.getNonce", params);
}

// ==================== Transactions ====================

RpcReply* AnimicaRpcClient::sendRawTransaction(const QString& signedTx)
{
    QJsonArray params;
    params.append(signedTx);
    return call("tx.sendRawTransaction", params);
}

RpcReply* AnimicaRpcClient::getTransaction(const QString& hash)
{
    QJsonArray params;
    params.append(hash);
    return call("tx.getTransactionByHash", params);
}

RpcReply* AnimicaRpcClient::getReceipt(const QString& hash)
{
    QJsonArray params;
    params.append(hash);
    return call("tx.getTransactionReceipt", params);
}

// ==================== P2P Network ====================

RpcReply* AnimicaRpcClient::listPeers()
{
    return call("p2p.listPeers", QJsonArray());
}

RpcReply* AnimicaRpcClient::getPeerCount()
{
    // Try multiple possible method names
    return call("p2p.peerCount", QJsonArray());
}

RpcReply* AnimicaRpcClient::getChainParams()
{
    return call("chain.getParams", QJsonArray());
}

// ==================== Private Methods ====================

RpcReply* AnimicaRpcClient::call(const QString& method)
{
    // No-parameter overload: use empty array as params
    return call(method, QJsonArray());
}

RpcReply* AnimicaRpcClient::call(const QString& method, const QJsonValue& params)
{
    QJsonObject request = buildRequest(method, params);
    RpcReply* reply = createReply(request);

    qDebug() << "RPC request:" << method << "to" << m_endpoint.toString();

    connect(reply, &RpcReply::finished, this, [this, method, reply]() {
        if (reply->error() == QNetworkReply::NoError) {
            updateConnectionState(true);
            return;
        }

        updateConnectionState(false);
        qWarning() << "RPC error for" << method << ":" << reply->errorString();
        emit error(reply->errorString());
    });

    reply->start();
    return reply;
}

// ==================== Synchronous JSON Wrappers ====================

QJsonValue AnimicaRpcClient::rpcCallSync(const QString& method, const QJsonValue& params)
{
    QJsonObject request = buildRequest(method, params);
    RpcReply* reply = createReply(request);

    // Block with event loop
    QEventLoop loop;
    QTimer timeoutTimer;
    timeoutTimer.setSingleShot(true);
    
    connect(reply, &RpcReply::finished, &loop, &QEventLoop::quit);
    connect(&timeoutTimer, &QTimer::timeout, &loop, &QEventLoop::quit);

    int totalTimeout = m_timeout * (m_maxRetries + 1) + m_backoffMs * (m_maxRetries * (m_maxRetries + 1) / 2);
    timeoutTimer.start(totalTimeout);
    reply->start();
    loop.exec();
    
    // Check for timeout
    if (!timeoutTimer.isActive()) {
        qWarning() << "RPC call timed out:" << method;
        reply->deleteLater();
        return QJsonValue();
    }
    
    timeoutTimer.stop();
    
    // Check for network error
    if (reply->error() != QNetworkReply::NoError) {
        qWarning() << "RPC error for" << method << ":" << reply->errorString();
        reply->deleteLater();
        updateConnectionState(false);
        return QJsonValue();
    }
    updateConnectionState(true);
    
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

QJsonObject AnimicaRpcClient::getTransactionStatusByHash(const QString& txHash)
{
    QJsonArray params;
    params.append(txHash);

    QJsonValue result = rpcCallSync("tx.getStatus", params);
    if (result.isObject()) {
        return result.toObject();
    }

    // Backward-compatibility fallback for nodes exposing only tx.getTransactionStatus.
    result = rpcCallSync("tx.getTransactionStatus", params);
    if (result.isObject()) {
        return result.toObject();
    }

    // Legacy fallback: infer status from tx.getTransactionByHash shape.
    result = rpcCallSync("tx.getTransactionByHash", params);
    if (result.isObject()) {
        const QJsonObject tx = result.toObject();
        if (!tx.isEmpty()) {
            QJsonObject normalized;
            const bool hasBlockRef = tx.contains("blockHash") || tx.contains("blockNumber");
            normalized["status"] = hasBlockRef ? "confirmed" : "pending";
            if (tx.contains("blockHash")) {
                normalized["blockHash"] = tx.value("blockHash");
            }
            if (tx.contains("blockNumber")) {
                normalized["blockNumber"] = tx.value("blockNumber");
            }
            return normalized;
        }
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

QNetworkRequest AnimicaRpcClient::buildNetworkRequest() const
{
    QNetworkRequest netRequest(m_endpoint);
    netRequest.setHeader(QNetworkRequest::ContentTypeHeader, "application/json");

    if (!m_username.isEmpty()) {
        QByteArray auth = (m_username + ":" + m_password).toUtf8().toBase64();
        netRequest.setRawHeader("Authorization", "Basic " + auth);
    }

    #if QT_VERSION >= QT_VERSION_CHECK(5, 15, 0)
    netRequest.setTransferTimeout(m_timeout);
    #endif

    return netRequest;
}

RpcReply* AnimicaRpcClient::createReply(const QJsonObject& request)
{
    QJsonDocument doc(request);
    QByteArray data = doc.toJson(QJsonDocument::Compact);
    QNetworkRequest netRequest = buildNetworkRequest();
    return new RpcReply(m_network, netRequest, data, m_timeout, m_maxRetries, m_backoffMs, this);
}

void AnimicaRpcClient::updateConnectionState(bool connected)
{
    if (m_connected == connected) {
        return;
    }
    m_connected = connected;
    if (connected) {
        emit this->connected();
    } else {
        emit this->disconnected();
    }
}
