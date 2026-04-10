#include "RpcReply.h"

#include <QNetworkAccessManager>
#include <QTimer>

namespace {

QString buildHttpStatusLabel(int httpStatusCode, const QString& httpReasonPhrase)
{
    const QString reason = httpReasonPhrase.trimmed();
    if (reason.isEmpty()) {
        return QString("HTTP %1").arg(httpStatusCode);
    }
    return QString("HTTP %1 %2").arg(httpStatusCode).arg(reason);
}

} // namespace

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
    , m_httpStatusCode(0)
{
}

QString RpcReply::describeNetworkError(
    QNetworkReply::NetworkError error,
    const QString& fallback,
    int httpStatusCode,
    const QString& httpReasonPhrase
)
{
    if (httpStatusCode > 0) {
        const QString statusLabel = buildHttpStatusLabel(httpStatusCode, httpReasonPhrase);
        if (httpStatusCode >= 500) {
            return QString("Hosted RPC unavailable (%1).").arg(statusLabel);
        }
        if (httpStatusCode == 429) {
            return QString("Hosted RPC rate limited the request (%1).").arg(statusLabel);
        }
        return QString("Hosted RPC request failed (%1).").arg(statusLabel);
    }

    switch (error) {
    case QNetworkReply::HostNotFoundError:
        return QStringLiteral("Could not resolve the hosted RPC hostname.");
    case QNetworkReply::TimeoutError:
        return QStringLiteral("Hosted RPC request timed out.");
    case QNetworkReply::ConnectionRefusedError:
        return QStringLiteral("Hosted RPC connection was refused.");
    case QNetworkReply::SslHandshakeFailedError:
        return QStringLiteral("TLS handshake with the hosted RPC failed.");
    default:
        break;
    }

    const QString normalizedFallback = fallback.trimmed();
    if (!normalizedFallback.isEmpty()) {
        return normalizedFallback;
    }
    return QStringLiteral("Hosted RPC request failed.");
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
    m_httpStatusCode = m_reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();
    m_httpReasonPhrase = m_reply->attribute(QNetworkRequest::HttpReasonPhraseAttribute).toString();
    if (m_reply->isReadable()) {
        m_response = m_reply->readAll();
    } else {
        m_response.clear();
    }
    m_errorString = describeNetworkError(
        m_error,
        m_reply->errorString(),
        m_httpStatusCode,
        m_httpReasonPhrase
    );

    m_reply->deleteLater();
    m_finished = true;
    emit finished();
}
