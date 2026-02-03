#ifndef RPCREPLY_H
#define RPCREPLY_H

#include <QObject>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QPointer>

class QNetworkAccessManager;

class RpcReply : public QObject
{
    Q_OBJECT

public:
    RpcReply(
        QNetworkAccessManager* network,
        const QNetworkRequest& request,
        const QByteArray& payload,
        int timeoutMs,
        int maxRetries,
        int backoffMs,
        QObject* parent = nullptr
    );

    void start();

    QNetworkReply::NetworkError error() const { return m_error; }
    QString errorString() const { return m_errorString; }
    QByteArray readAll() const { return m_response; }
    bool isFinished() const { return m_finished; }

signals:
    void finished();

private:
    void issueRequest();
    void handleReplyFinished();

    QNetworkAccessManager* m_network;
    QNetworkRequest m_request;
    QByteArray m_payload;
    int m_timeoutMs;
    int m_maxRetries;
    int m_backoffMs;
    int m_attempts;
    QNetworkReply::NetworkError m_error;
    QString m_errorString;
    QByteArray m_response;
    bool m_finished;
    QPointer<QNetworkReply> m_reply;
};

#endif // RPCREPLY_H
