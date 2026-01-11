#pragma once

#include <QObject>
#include <QJsonObject>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QPointer>
#include <QTimer>

namespace animica::nodekit {

enum class RpcErrorType {
    None,
    Network,
    Parse,
    Rpc,
    Timeout
};

struct RpcError {
    RpcErrorType type = RpcErrorType::None;
    QString message;
    int code = 0;
};

class RpcClient : public QObject {
    Q_OBJECT

public:
    explicit RpcClient(QObject *parent = nullptr);

    void setEndpoint(const QUrl &url);
    void setTimeoutMs(int timeoutMs);
    void setRetryCount(int retries);

    QNetworkReply *call(const QString &method, const QJsonObject &params = {});
    QNetworkReply *chainGetHead();
    QNetworkReply *chainGetSafeHead();
    QNetworkReply *syncStatus();
    QNetworkReply *stateGetBalance(const QString &address);
    QNetworkReply *txSend(const QJsonObject &signedTx);
    QNetworkReply *mempoolList();
    QNetworkReply *peerList();

    static bool parseResponse(const QByteArray &payload, QJsonObject *result, RpcError *error);

signals:
    void rpcError(const RpcError &error);
    void rpcResponse(const QString &method, const QJsonObject &result);

private slots:
    void handleFinished();

private:
    QByteArray buildRequest(const QString &method, const QJsonObject &params, int id) const;
    void handleReply(QNetworkReply *reply);

    QNetworkAccessManager network_{};
    QUrl endpoint_{};
    int timeoutMs_ = 8000;
    int retryCount_ = 2;
    int nextId_ = 1;
};

} // namespace animica::nodekit
