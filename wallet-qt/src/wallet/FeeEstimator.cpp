#include "FeeEstimator.h"
#include "../rpc/AnimicaRpcClient.h"
#include "../rpc/RpcReply.h"
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonValue>
#include <QEventLoop>
#include <QTimer>
#include <QDateTime>
#include <QMutexLocker>
#include <QDebug>
#include <limits>

FeeEstimator::FeeEstimator(AnimicaRpcClient* rpcClient, QObject* parent)
    : QObject(parent)
    , m_rpcClient(rpcClient)
    , m_cachedBaseFee(0)
    , m_cacheTimestamp(0)
    , m_cacheDuration(60)
{
}

FeeEstimator::~FeeEstimator()
{
}

qint64 FeeEstimator::getGasPrice(FeeTier tier)
{
    const qint64 baseFee = qMax<qint64>(1, getBaseFee());
    
    switch (tier) {
        case Slow:
            return baseFee;
        case Normal:
            return baseFee * 2;
        case Fast:
            return baseFee * 5;
        default:
            return baseFee;
    }
}

qint64 FeeEstimator::getBaseFee()
{
    QMutexLocker locker(&m_mutex);
    
    if (isCacheValid()) {
        return m_cachedBaseFee;
    }
    
    locker.unlock();
    refreshBaseFee();
    locker.relock();
    
    return m_cachedBaseFee;
}

qint64 FeeEstimator::calculateFee(FeeTier tier, qint64 gasLimit)
{
    const qint64 gasPrice = getGasPrice(tier);
    if (gasLimit <= 0) {
        return gasPrice;
    }
    if (gasPrice > std::numeric_limits<qint64>::max() / gasLimit) {
        return std::numeric_limits<qint64>::max();
    }
    return gasPrice * gasLimit;
}

QString FeeEstimator::formatFee(qint64 feeWei)
{
    if (feeWei < 1000) {
        return QString::number(feeWei) + " wei";
    } else if (feeWei < 1000000) {
        return QString::number(feeWei / 1000.0, 'f', 3) + " kwei";
    } else if (feeWei < 1000000000) {
        return QString::number(feeWei / 1000000.0, 'f', 3) + " mwei";
    } else {
        return QString::number(feeWei / 1000000000.0, 'f', 6) + " gwei";
    }
}

QString FeeEstimator::formatFeeANM(qint64 feeWei)
{
    // 1 ANM = 10^9 wei
    double anm = feeWei / 1e9;
    return QString::number(anm, 'f', 9) + " ANM";
}

void FeeEstimator::setCacheDuration(int seconds)
{
    QMutexLocker locker(&m_mutex);
    m_cacheDuration = seconds;
}

void FeeEstimator::refreshBaseFee()
{
    constexpr qint64 kFallbackGasPrice = 1;

    if (!m_rpcClient) {
        m_lastError = "RPC client not available";
        emit error(m_lastError);
        QMutexLocker locker(&m_mutex);
        m_cachedBaseFee = kFallbackGasPrice;
        m_cacheTimestamp = getCurrentTimestamp();
        return;
    }
    
    // Try to get chain parameters
    RpcReply* reply = m_rpcClient->getChainParams();
    
    if (!reply) {
        m_lastError = "Failed to create RPC request";
        emit error(m_lastError);
        QMutexLocker locker(&m_mutex);
        m_cachedBaseFee = kFallbackGasPrice;
        m_cacheTimestamp = getCurrentTimestamp();
        return;
    }
    
    // Wait for reply synchronously with timeout
    QEventLoop loop;
    QTimer timer;
    timer.setSingleShot(true);
    
    connect(reply, &RpcReply::finished, &loop, &QEventLoop::quit);
    connect(&timer, &QTimer::timeout, &loop, &QEventLoop::quit);
    
    timer.start(5000); // 5 second timeout
    loop.exec();
    
    if (!timer.isActive()) {
        // Timeout
        m_lastError = "RPC request timed out";
        emit error(m_lastError);
        QMutexLocker locker(&m_mutex);
        m_cachedBaseFee = kFallbackGasPrice;
        m_cacheTimestamp = getCurrentTimestamp();
        reply->deleteLater();
        return;
    }
    
    timer.stop();
    
    if (reply->error() != QNetworkReply::NoError) {
        m_lastError = "RPC error: " + reply->errorString();
        emit error(m_lastError);
        QMutexLocker locker(&m_mutex);
        m_cachedBaseFee = kFallbackGasPrice;
        m_cacheTimestamp = getCurrentTimestamp();
        reply->deleteLater();
        return;
    }
    
    QByteArray data = reply->readAll();
    reply->deleteLater();
    
    QJsonDocument doc = QJsonDocument::fromJson(data);
    if (!doc.isObject()) {
        m_lastError = "Invalid JSON response";
        emit error(m_lastError);
        QMutexLocker locker(&m_mutex);
        m_cachedBaseFee = kFallbackGasPrice;
        m_cacheTimestamp = getCurrentTimestamp();
        return;
    }
    
    QJsonObject obj = doc.object();
    
    // Check for error
    if (obj.contains("error")) {
        QJsonObject errorObj = obj["error"].toObject();
        m_lastError = "RPC error: " + errorObj["message"].toString();
        emit error(m_lastError);
        QMutexLocker locker(&m_mutex);
        m_cachedBaseFee = kFallbackGasPrice;
        m_cacheTimestamp = getCurrentTimestamp();
        return;
    }
    
    // Try to extract min_gas_price from result, with nested fallbacks.
    qint64 minGasPrice = kFallbackGasPrice;
    auto parseInt = [](const QJsonValue& value, bool* okOut = nullptr) -> qint64 {
        bool ok = false;
        qint64 parsed = 0;
        if (value.isDouble()) {
            parsed = static_cast<qint64>(value.toDouble());
            ok = true;
        } else if (value.isString()) {
            const QString text = value.toString().trimmed();
            if (text.startsWith("0x") || text.startsWith("0X")) {
                parsed = text.mid(2).toLongLong(&ok, 16);
            } else {
                parsed = text.toLongLong(&ok);
            }
        }
        if (okOut) {
            *okOut = ok;
        }
        return parsed;
    };
    auto maybeReadGasPrice = [&parseInt](const QJsonObject& source, qint64* out) -> bool {
        if (!out) {
            return false;
        }
        for (const QString& key : {QStringLiteral("min_gas_price"), QStringLiteral("minGasPrice")}) {
            if (!source.contains(key)) {
                continue;
            }
            bool ok = false;
            const qint64 value = parseInt(source.value(key), &ok);
            if (ok && value > 0) {
                *out = value;
                return true;
            }
        }
        return false;
    };

    if (obj.contains("result")) {
        const QJsonValue result = obj["result"];
        if (result.isObject()) {
            const QJsonObject params = result.toObject();
            if (!maybeReadGasPrice(params, &minGasPrice)) {
                for (const QString& nested : {QStringLiteral("block"), QStringLiteral("mempool"), QStringLiteral("fees")}) {
                    const QJsonValue nestedValue = params.value(nested);
                    if (nestedValue.isObject() && maybeReadGasPrice(nestedValue.toObject(), &minGasPrice)) {
                        break;
                    }
                }
            }
        }
    }
    
    QMutexLocker locker(&m_mutex);
    m_cachedBaseFee = qMax<qint64>(1, minGasPrice);
    m_cacheTimestamp = getCurrentTimestamp();
    m_lastError.clear();
    
    qDebug() << "Base fee updated:" << m_cachedBaseFee << "wei";
    emit baseFeeUpdated(m_cachedBaseFee);
}

bool FeeEstimator::isCacheValid() const
{
    if (m_cachedBaseFee == 0) {
        return false;
    }
    
    qint64 age = getCurrentTimestamp() - m_cacheTimestamp;
    return age < m_cacheDuration;
}

qint64 FeeEstimator::getCurrentTimestamp() const
{
    return QDateTime::currentSecsSinceEpoch();
}
