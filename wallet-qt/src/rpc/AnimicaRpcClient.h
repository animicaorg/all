#ifndef ANIMICARPCCLIENT_H
#define ANIMICARPCCLIENT_H

#include <QObject>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QJsonObject>
#include <QUrl>

/**
 * @brief HTTP JSON-RPC client for Animica node.
 * 
 * Provides type-safe wrapper around Animica RPC methods.
 * Uses Qt's QNetworkAccessManager for HTTP communication.
 * 
 * All methods return QNetworkReply* which can be used to:
 * - Connect to finished() signal
 * - Read response with readAll()
 * - Parse JSON with QJsonDocument
 * 
 * Example usage:
 * 
 *   AnimicaRpcClient client;
 *   client.setEndpoint("http://127.0.0.1:8545/rpc");
 *   
 *   QNetworkReply* reply = client.ping();
 *   connect(reply, &QNetworkReply::finished, [reply]() {
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
     * @brief Set request timeout in milliseconds.
     * @param timeout Timeout in ms (default: 30000)
     */
    void setTimeout(int timeout) { m_timeout = timeout; }

    // ==================== Health & System ====================

    /**
     * @brief Health check ping.
     * @return Network reply (expect: {"result": "pong"})
     */
    QNetworkReply* ping();

    // ==================== Chain Information ====================

    /**
     * @brief Get network chain ID.
     * @return Network reply (expect: {"result": <integer>})
     */
    QNetworkReply* getChainId();

    /**
     * @brief Get current chain head (latest block).
     * @return Network reply (expect: {"result": {block object}})
     */
    QNetworkReply* getHead();

    /**
     * @brief Get block by number.
     * @param number Block number or "latest"
     * @param fullTx Include full transaction objects
     * @return Network reply
     */
    QNetworkReply* getBlockByNumber(const QString& number, bool fullTx = false);

    /**
     * @brief Get block by hash.
     * @param hash Block hash (hex with 0x prefix)
     * @param fullTx Include full transaction objects
     * @return Network reply
     */
    QNetworkReply* getBlockByHash(const QString& hash, bool fullTx = false);

    // ==================== Sync Status ====================

    /**
     * @brief Get synchronization status.
     * @return Network reply (expect: {"result": {sync status}})
     */
    QNetworkReply* getSyncStatus();

    // ==================== State Queries ====================

    /**
     * @brief Get account balance.
     * @param address Account address (Bech32 format)
     * @param block Block specifier ("latest", "pending", or number)
     * @return Network reply (expect: {"result": "<balance in wei>"})
     */
    QNetworkReply* getBalance(const QString& address, const QString& block = "latest");

    /**
     * @brief Get account nonce (transaction count).
     * @param address Account address (Bech32 format)
     * @param block Block specifier ("latest", "pending", or number)
     * @return Network reply (expect: {"result": <nonce>})
     */
    QNetworkReply* getNonce(const QString& address, const QString& block = "latest");

    // ==================== Transactions ====================

    /**
     * @brief Send signed transaction.
     * @param signedTx Signed transaction bytes (hex with 0x prefix)
     * @return Network reply (expect: {"result": "<tx hash>"})
     */
    QNetworkReply* sendRawTransaction(const QString& signedTx);

    /**
     * @brief Get transaction by hash.
     * @param hash Transaction hash (hex with 0x prefix)
     * @return Network reply (expect: {"result": {tx object}})
     */
    QNetworkReply* getTransaction(const QString& hash);

    /**
     * @brief Get transaction receipt.
     * @param hash Transaction hash (hex with 0x prefix)
     * @return Network reply (expect: {"result": {receipt} or null})
     */
    QNetworkReply* getReceipt(const QString& hash);

    // ==================== P2P Network ====================

    /**
     * @brief List connected peers.
     * @return Network reply (expect: {"result": [peer objects]})
     */
    QNetworkReply* listPeers();

    /**
     * @brief Get peer count.
     * @return Network reply (expect: {"result": <count>})
     */
    QNetworkReply* getPeerCount();

    /**
     * @brief Get chain parameters.
     * @return Network reply (expect: {"result": {params object}})
     */
    QNetworkReply* getChainParams();

    /**
     * @brief Execute custom RPC call.
     * @param method RPC method name
     * @param params Parameters (array or object)
     * @return Network reply
     */
    QNetworkReply* call(const QString& method, const QJsonValue& params = QJsonArray());

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

    /**
     * @brief Get next request ID.
     * @return Monotonically increasing request ID
     */
    int nextId();

    // ==================== Member Variables ====================
    
    QNetworkAccessManager* m_network;
    QUrl m_endpoint;
    int m_timeout;
    int m_requestId;
};

#endif // ANIMICARPCCLIENT_H
