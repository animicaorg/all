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
    qint64 baseFee = getBaseFee();
    
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
    Q_UNUSED(gasLimit);
    return getGasPrice(tier);
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
    if (!m_rpcClient) {
        m_lastError = "RPC client not available";
        emit error(m_lastError);
        QMutexLocker locker(&m_mutex);
        m_cachedBaseFee = 1000000; // Conservative default: 1M wei
        m_cacheTimestamp = getCurrentTimestamp();
        return;
    }
    
    // Try to get chain parameters
    RpcReply* reply = m_rpcClient->getChainParams();
    
    if (!reply) {
        m_lastError = "Failed to create RPC request";
        emit error(m_lastError);
        QMutexLocker locker(&m_mutex);
        m_cachedBaseFee = 1000000; // Conservative default
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
        m_cachedBaseFee = 1000000;
        m_cacheTimestamp = getCurrentTimestamp();
        reply->deleteLater();
        return;
    }
    
    timer.stop();
    
    if (reply->error() != QNetworkReply::NoError) {
        m_lastError = "RPC error: " + reply->errorString();
        emit error(m_lastError);
        QMutexLocker locker(&m_mutex);
        m_cachedBaseFee = 1000000;
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
        m_cachedBaseFee = 1000000;
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
        m_cachedBaseFee = 1000000;
        m_cacheTimestamp = getCurrentTimestamp();
        return;
    }
    
    // Try to extract min_gas_price from result
    qint64 minGasPrice = 1000000; // Default
    
    if (obj.contains("result")) {
        QJsonValue result = obj["result"];
        if (result.isObject()) {
            QJsonObject params = result.toObject();
            if (params.contains("min_gas_price")) {
                QJsonValue minGasPriceVal = params["min_gas_price"];
                if (minGasPriceVal.isDouble()) {
                    minGasPrice = static_cast<qint64>(minGasPriceVal.toDouble());
                } else if (minGasPriceVal.isString()) {
                    bool ok;
                    minGasPrice = minGasPriceVal.toString().toLongLong(&ok);
                    if (!ok) {
                        minGasPrice = 1000000;
                    }
                }
            }
        }
    }
    
    QMutexLocker locker(&m_mutex);
    m_cachedBaseFee = minGasPrice;
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
