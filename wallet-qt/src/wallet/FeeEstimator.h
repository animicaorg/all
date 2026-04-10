#ifndef FEEESTIMATOR_H
#define FEEESTIMATOR_H

#include <QObject>
#include <QString>
#include <QMutex>

class AnimicaRpcClient;

/**
 * @brief Fee estimation for transaction sending.
 * 
 * Provides tiered fee estimation based on chain parameters and user preference.
 * Caches base fee to reduce RPC calls while maintaining reasonable freshness.
 * 
 * Fee tiers:
 * - Slow: Base fee (minimum)
 * - Normal: 2x base fee
 * - Fast: 5x base fee
 */
class FeeEstimator : public QObject
{
    Q_OBJECT
    
public:
    enum FeeTier {
        Slow,    // Minimum fee
        Normal,  // 2x base fee
        Fast     // 5x base fee
    };
    Q_ENUM(FeeTier)
    
    explicit FeeEstimator(AnimicaRpcClient* rpcClient, QObject* parent = nullptr);
    ~FeeEstimator();
    
    /**
     * @brief Get max-fee-per-gas scalar for a tier.
     * @param tier Fee tier
     * @return Gas price in base units per gas
     */
    qint64 getGasPrice(FeeTier tier);
    
    /**
     * @brief Get base fee from chain.
     * @return Base fee in wei (cached if within cache duration)
     */
    qint64 getBaseFee();
    
    /**
     * @brief Calculate fee from a tier.
     * @param tier Fee tier
     * @param gasLimit Gas limit used to compute reserve (gasLimit * price).
     * @return Fee reserve in base units
     */
    qint64 calculateFee(FeeTier tier, qint64 gasLimit);
    
    /**
     * @brief Format fee for display.
     * @param feeWei Fee in wei
     * @return Formatted string (e.g., "0.000021 wei")
     */
    QString formatFee(qint64 feeWei);
    
    /**
     * @brief Format fee in ANM tokens.
     * @param feeWei Fee in wei
     * @return Formatted string (e.g., "0.000000021 ANM")
     */
    QString formatFeeANM(qint64 feeWei);
    
    /**
     * @brief Standard transfer gas limit.
     * @return 21000 (standard ETH-style transfer)
     */
    static qint64 standardTransferGas() { return 21000; }
    
    /**
     * @brief Contract call gas limit estimate.
     * @return 100000
     */
    static qint64 contractCallGas() { return 100000; }
    
    /**
     * @brief Contract deployment gas limit estimate.
     * @return 2000000
     */
    static qint64 contractDeployGas() { return 2000000; }
    
    /**
     * @brief Set cache duration.
     * @param seconds Duration in seconds (default: 60)
     */
    void setCacheDuration(int seconds);
    
    /**
     * @brief Get last error message.
     * @return Error message or empty if no error
     */
    QString lastError() const { return m_lastError; }
    
signals:
    /**
     * @brief Emitted when base fee is updated.
     * @param newBaseFee New base fee in wei
     */
    void baseFeeUpdated(qint64 newBaseFee);
    
    /**
     * @brief Emitted on error.
     * @param message Error message
     */
    void error(const QString& message);
    
private:
    void refreshBaseFee();
    bool isCacheValid() const;
    qint64 getCurrentTimestamp() const;
    
    AnimicaRpcClient* m_rpcClient;
    qint64 m_cachedBaseFee;
    qint64 m_cacheTimestamp;
    int m_cacheDuration;
    QString m_lastError;
    mutable QMutex m_mutex;
};

#endif // FEEESTIMATOR_H
