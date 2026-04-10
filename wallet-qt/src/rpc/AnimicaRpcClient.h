#ifndef ANIMICARPCCLIENT_H
#define ANIMICARPCCLIENT_H

#include <QObject>
#include <QNetworkAccessManager>
#include "RpcReply.h"
#include <QJsonObject>
#include <QJsonValue>
#include <QUrl>

/**
 * @brief HTTP JSON-RPC client for Animica node.
 * 
 * Provides type-safe wrapper around Animica RPC methods.
 * Uses Qt's QNetworkAccessManager for HTTP communication.
 * 
 * All methods return RpcReply* which can be used to:
 * - Connect to finished() signal
 * - Read response with readAll()
 * - Parse JSON with QJsonDocument
 * 
 * Example usage:
 * 
 *   AnimicaRpcClient client;
 *   client.setEndpoint("http://127.0.0.1:8545/rpc");
 *   
 *   RpcReply* reply = client.ping();
 *   connect(reply, &RpcReply::finished, [reply]() {
 *       if (reply->error() == QNetworkReply::NoError) {
 *           QJsonDocument doc = QJsonDocument::fromJson(reply->readAll());
 *           // Process response...
 *       }
 *       reply->deleteLater();
 *   });
 */
class AnimicaRpcClient : public QObject
{
    Q_OBJECT

public:
    explicit AnimicaRpcClient(QObject* parent = nullptr);
    ~AnimicaRpcClient() override;

    /**
     * @brief Set the RPC endpoint URL.
     * @param url Full RPC URL (e.g., "http://127.0.0.1:8545/rpc")
     */
    void setEndpoint(const QString& url);

    /**
     * @brief Get the current RPC endpoint URL.
     * @return Current endpoint URL
     */
    QString endpoint() const { return m_endpoint.toString(); }

    /**
     * @brief Set retry policy for RPC calls.
     * @param maxRetries Maximum retries after initial attempt
     * @param backoffMs Base backoff delay in ms
     */
    void setRetryPolicy(int maxRetries, int backoffMs);

    /**
     * @brief Set request timeout in milliseconds.
     * @param timeout Timeout in ms (default: 8000)
     */
    void setTimeout(int timeout) { m_timeout = timeout; }

    // ==================== Health & System ====================

    /**
     * @brief Health check ping.
     * @return Network reply (expect: {"result": "pong"})
     */
    RpcReply* ping();

    // ==================== Chain Information ====================

    /**
     * @brief Get network chain ID.
     * @return Network reply (expect: {"result": <integer>})
     */
    RpcReply* getChainId();

    /**
     * @brief Get current chain head (latest block).
     * @return Network reply (expect: {"result": {block object}})
     */
    RpcReply* getHead();

    /**
     * @brief Get block by number.
     * @param number Block number or "latest"
     * @param fullTx Include full transaction objects
     * @return Network reply
     */
    RpcReply* getBlockByNumber(const QString& number, bool fullTx = false);

    /**
     * @brief Get block by hash.
     * @param hash Block hash (hex with 0x prefix)
     * @param fullTx Include full transaction objects
     * @return Network reply
     */
    RpcReply* getBlockByHash(const QString& hash, bool fullTx = false);

    // ==================== Sync Status ====================

    /**
     * @brief Get synchronization status.
     * @return Network reply (expect: {"result": {sync status}})
     */
    RpcReply* getSyncStatus();

    // ==================== State Queries ====================

    /**
     * @brief Get account balance.
     * @param address Account address (Bech32 format)
     * @param block Block specifier ("latest", "pending", or number)
     * @return Network reply (expect: {"result": "<balance in wei>"})
     */
    RpcReply* getBalance(const QString& address, const QString& block = "latest");

    /**
     * @brief Get account nonce (transaction count).
     * @param address Account address (Bech32 format)
     * @param block Block specifier ("latest", "pending", or number)
     * @return Network reply (expect: {"result": <nonce>})
     */
    RpcReply* getNonce(const QString& address, const QString& block = "latest");

    // ==================== Transactions ====================

    /**
     * @brief Send signed transaction.
     * @param signedTx Signed transaction bytes (hex with 0x prefix)
     * @return Network reply (expect: {"result": "<tx hash>"})
     */
    RpcReply* sendRawTransaction(const QString& signedTx);

    /**
     * @brief Get transaction by hash.
     * @param hash Transaction hash (hex with 0x prefix)
     * @return Network reply (expect: {"result": {tx object}})
     */
    RpcReply* getTransaction(const QString& hash);

    /**
     * @brief Get transaction receipt.
     * @param hash Transaction hash (hex with 0x prefix)
     * @return Network reply (expect: {"result": {receipt} or null})
     */
    RpcReply* getReceipt(const QString& hash);

    // ==================== P2P Network ====================

    /**
     * @brief List connected peers.
     * @return Network reply (expect: {"result": [peer objects]})
     */
    RpcReply* listPeers();

    /**
     * @brief Get peer count.
     * @return Network reply (expect: {"result": <count>})
     */
    RpcReply* getPeerCount();

    /**
     * @brief Get chain parameters.
     * @return Network reply (expect: {"result": {params object}})
     */
    RpcReply* getChainParams();

    /**
     * @brief Execute custom RPC call.
     * @param method RPC method name
     * @param params Parameters (array or object)
     * @return Network reply
     */
    RpcReply* call(const QString& method, const QJsonValue& params);
    
    /**
     * @brief Execute custom RPC call with no parameters.
     * @param method RPC method name
     * @return Network reply
     */
    RpcReply* call(const QString& method);
    
    // ==================== Synchronous JSON Wrappers ====================
    
    /**
     * @brief Get chain head synchronously.
     * @return JSON object with block data or empty object on error
     */
    QJsonObject getHeadJson();
    
    /**
     * @brief Get block by number synchronously.
     * @param number Block number
     * @param fullTx Include full transaction objects
     * @return JSON object with block data or empty object on error
     */
    QJsonObject getBlockByNumberJson(qint64 number, bool fullTx = false);
    
    /**
     * @brief Get transaction by hash synchronously.
     * @param txHash Transaction hash (hex with 0x prefix)
     * @return JSON object with transaction data or empty object on error
     */
    QJsonObject getTransactionByHash(const QString& txHash);

    /**
     * @brief Get transaction status synchronously.
     * @param txHash Transaction hash (hex with 0x prefix)
     * @return JSON object with status data or empty object on error
     */
    QJsonObject getTransactionStatusByHash(const QString& txHash);

signals:
    /**
     * @brief Emitted when successfully connected to node.
     */
    void connected();

    /**
     * @brief Emitted when connection to node is lost.
     */
    void disconnected();

    /**
     * @brief Emitted on RPC error.
     * @param message Error message
     */
    void error(const QString& message);

private:
    // ==================== Private Methods ====================
    // Note: These methods were moved out of signals: section to fix MOC compilation.
    // MOC requires signals: sections to contain only signal (function) declarations.
    
    /**
     * @brief Build JSON-RPC request.
     * @param method RPC method name
     * @param params Parameters (array or object)
     * @return JSON request object
     */
    QJsonObject buildRequest(const QString& method, const QJsonValue& params);

    QNetworkRequest buildNetworkRequest() const;
    RpcReply* createReply(const QJsonObject& request);
    void updateConnectionState(bool connected);

    /**
     * @brief Get next request ID.
     * @return Monotonically increasing request ID
     */
    int nextId();
    
    /**
     * @brief Execute synchronous RPC call with timeout.
     * @param method RPC method name
     * @param params Parameters (array or object)
     * @return JSON result value or null on error
     */
    QJsonValue rpcCallSync(const QString& method, const QJsonValue& params);

    // ==================== Member Variables ====================
    
    QNetworkAccessManager* m_network;
    QUrl m_endpoint;
    QString m_username;
    QString m_password;
    int m_timeout;
    int m_maxRetries;
    int m_backoffMs;
    int m_requestId;
    bool m_connected;
};

#endif // ANIMICARPCCLIENT_H
