#include "AnimicaNodeKit/RpcClient.h"

#include <QJsonDocument>

namespace animica::nodekit {

RpcClient::RpcClient(QObject *parent) : QObject(parent) {}

void RpcClient::setEndpoint(const QUrl &url) {
    endpoint_ = url;
}

void RpcClient::setTimeoutMs(int timeoutMs) {
    timeoutMs_ = timeoutMs;
}

void RpcClient::setRetryCount(int retries) {
    retryCount_ = retries;
}

QNetworkReply *RpcClient::call(const QString &method, const QJsonObject &params) {
    if (!endpoint_.isValid()) {
        emit rpcError({RpcErrorType::Network, QStringLiteral("Invalid RPC endpoint"), 0});
        return nullptr;
    }

    const int id = nextId_++;
    QNetworkRequest request(endpoint_);
    request.setHeader(QNetworkRequest::ContentTypeHeader, "application/json");

    QNetworkReply *reply = network_.post(request, buildRequest(method, params, id));
    reply->setProperty("rpc_method", method);
    reply->setProperty("rpc_retries", 0);

    QTimer::singleShot(timeoutMs_, reply, [this, reply]() {
        if (reply && reply->isRunning()) {
            reply->abort();
            emit rpcError({RpcErrorType::Timeout, QStringLiteral("RPC timeout"), 0});
        }
    });

    connect(reply, &QNetworkReply::finished, this, &RpcClient::handleFinished);
    return reply;
}

QNetworkReply *RpcClient::chainGetHead() {
    return call(QStringLiteral("chain.getHead"));
}

QNetworkReply *RpcClient::chainGetSafeHead() {
    return call(QStringLiteral("chain.getSafeHead"));
}

QNetworkReply *RpcClient::syncStatus() {
    return call(QStringLiteral("sync.status"));
}

QNetworkReply *RpcClient::stateGetBalance(const QString &address) {
    return call(QStringLiteral("state.getBalance"), QJsonObject{{"address", address}});
}

QNetworkReply *RpcClient::txSend(const QJsonObject &signedTx) {
    return call(QStringLiteral("tx.send"), QJsonObject{{"tx", signedTx}});
}

QNetworkReply *RpcClient::mempoolList() {
    return call(QStringLiteral("mempool.list"));
}

QNetworkReply *RpcClient::peerList() {
    return call(QStringLiteral("peer.list"));
}

void RpcClient::handleFinished() {
    auto *reply = qobject_cast<QNetworkReply *>(sender());
    if (!reply) {
        return;
    }
    handleReply(reply);
    reply->deleteLater();
}

QByteArray RpcClient::buildRequest(const QString &method, const QJsonObject &params, int id) const {
    QJsonObject root{{"jsonrpc", "2.0"}, {"method", method}, {"params", params}, {"id", id}};
    return QJsonDocument(root).toJson(QJsonDocument::Compact);
}

void RpcClient::handleReply(QNetworkReply *reply) {
    const QString method = reply->property("rpc_method").toString();
    if (reply->error() != QNetworkReply::NoError) {
        const int retries = reply->property("rpc_retries").toInt();
        if (retries < retryCount_) {
            QNetworkRequest request(endpoint_);
            request.setHeader(QNetworkRequest::ContentTypeHeader, "application/json");
            QNetworkReply *retry = network_.post(request, reply->readAll());
            retry->setProperty("rpc_method", method);
            retry->setProperty("rpc_retries", retries + 1);
            connect(retry, &QNetworkReply::finished, this, &RpcClient::handleFinished);
            return;
        }
        emit rpcError({RpcErrorType::Network, reply->errorString(), reply->error()});
        return;
    }

    const QByteArray payload = reply->readAll();
    QJsonObject result;
    RpcError error;
    if (!parseResponse(payload, &result, &error)) {
        emit rpcError(error);
        return;
    }
    emit rpcResponse(method, result);
}

bool RpcClient::parseResponse(const QByteArray &payload, QJsonObject *result, RpcError *error) {
    const QJsonDocument doc = QJsonDocument::fromJson(payload);
    if (!doc.isObject()) {
        if (error) {
            *error = {RpcErrorType::Parse, QStringLiteral("Invalid JSON-RPC response"), 0};
        }
        return false;
    }

    const QJsonObject obj = doc.object();
    if (obj.contains("error")) {
        const QJsonObject err = obj.value("error").toObject();
        if (error) {
            *error = {RpcErrorType::Rpc, err.value("message").toString(), err.value("code").toInt()};
        }
        return false;
    }

    if (result) {
        *result = obj.value("result").toObject();
    }
    return true;
}

} // namespace animica::nodekit
