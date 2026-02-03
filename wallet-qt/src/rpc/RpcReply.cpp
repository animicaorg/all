#include "RpcReply.h"
#include <QNetworkAccessManager>
#include <QTimer>

RpcReply::RpcReply(
    QNetworkAccessManager* network,
    const QNetworkRequest& request,
    const QByteArray& payload,
    int timeoutMs,
    int maxRetries,
    int backoffMs,
    QObject* parent
)
    : QObject(parent)
    , m_network(network)
    , m_request(request)
    , m_payload(payload)
    , m_timeoutMs(timeoutMs)
    , m_maxRetries(maxRetries)
    , m_backoffMs(backoffMs)
    , m_attempts(0)
    , m_error(QNetworkReply::NoError)
    , m_finished(false)
{
}

void RpcReply::start()
{
    issueRequest();
}

void RpcReply::issueRequest()
{
    if (!m_network) {
        m_error = QNetworkReply::UnknownNetworkError;
        m_errorString = "Network manager unavailable";
        m_finished = true;
        emit finished();
        return;
    }

    m_attempts += 1;
    m_reply = m_network->post(m_request, m_payload);

    connect(m_reply, &QNetworkReply::finished, this, &RpcReply::handleReplyFinished);
}

void RpcReply::handleReplyFinished()
{
    if (!m_reply) {
        m_error = QNetworkReply::UnknownNetworkError;
        m_errorString = "RPC reply missing";
        m_finished = true;
        emit finished();
        return;
    }

    const bool hasError = (m_reply->error() != QNetworkReply::NoError);
    if (hasError && m_attempts <= m_maxRetries) {
        int delayMs = m_backoffMs * m_attempts;
        m_reply->deleteLater();
        QTimer::singleShot(delayMs, this, &RpcReply::issueRequest);
        return;
    }

    m_error = m_reply->error();
    m_errorString = m_reply->errorString();
    if (!hasError) {
        m_response = m_reply->readAll();
    }

    m_reply->deleteLater();
    m_finished = true;
    emit finished();
}
