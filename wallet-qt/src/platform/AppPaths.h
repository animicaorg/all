#ifndef APPPATHS_H
#define APPPATHS_H

#include <QString>
#include <QDir>

/**
 * @brief Cross-platform application path resolution.
 * 
 * Provides OS-appropriate base directories for:
 * - Node data (chain DB, P2P state, when embedded node is enabled)
 * - Wallet data (keys, accounts, settings)
 * - Logs (wallet and node logs)
 * - Runtime state (PID files, lock files, ports)
 * 
 * Directory structure:
 * 
 * <base>/
 *   ├── node/           # Node data (embedded node builds only)
 *   │   ├── chain-1/    # Mainnet
 *   │   ├── chain-2/    # Testnet
 *   │   └── chain-1337/ # Devnet
 *   ├── wallet/         # Wallet-specific data
 *   │   ├── keystore/
 *   │   └── accounts.db
 *   ├── logs/           # All logs
 *   │   ├── wallet.log
 *   │   └── node-*.log
 *   └── run/            # Runtime state (embedded node builds only)
 *       ├── node.json
 *       ├── node.lock
 *       └── node.pid
 * 
 * OS-specific base paths:
 * - macOS: ~/Library/Application Support/AnimicaWallet
 * - Windows: %APPDATA%/AnimicaWallet
 * - Linux: ~/.local/share/AnimicaWallet
 */
class AppPaths
{
public:
    /**
     * @brief Get the base application data directory.
     * @return Base directory path (creates if doesn't exist)
     */
    static QString baseDir();

    /**
     * @brief Get the node data directory.
     * @return Node data directory path (creates if doesn't exist)
     */
    static QString nodeDir();

    /**
     * @brief Get the node data directory for a specific chain.
     * @param chainId Chain ID (1=mainnet, 2=testnet, 1337=devnet)
     * @return Chain-specific node data directory
     */
    static QString nodeChainDir(int chainId);

    /**
     * @brief Get the wallet data directory.
     * @return Wallet data directory path (creates if doesn't exist)
     */
    static QString walletDir();

    /**
     * @brief Get the logs directory.
     * @return Logs directory path (creates if doesn't exist)
     */
    static QString logsDir();

    /**
     * @brief Get the runtime state directory.
     * @return Runtime directory path (creates if doesn't exist)
     */
    static QString runDir();

    /**
     * @brief Get the node log file path.
     * @param network Network name (mainnet, testnet, devnet)
     * @return Log file path
     */
    static QString nodeLogFile(const QString& network);

    /**
     * @brief Get the wallet log file path.
     * @return Log file path
     */
    static QString walletLogFile();

    /**
     * @brief Get the node PID file path.
     * @return PID file path
     */
    static QString nodePidFile();

    /**
     * @brief Get the node lock file path.
     * @return Lock file path
     */
    static QString nodeLockFile();

    /**
     * @brief Get the node runtime info JSON file path.
     * @return JSON file path
     */
    static QString nodeInfoFile();

    /**
     * @brief Get the bundled node path (where animica executable is).
     * @return Bundled node directory path
     */
    static QString getBundledNodePath();

    /**
     * @brief Get the bundled Python interpreter inside the node runtime.
     * @return Bundled Python path, or empty if unavailable
     */
    static QString bundledPythonPath();

    /**
     * @brief Get the bundled runtime assets directory.
     * @return Bundled node assets directory, or empty if unavailable
     */
    static QString bundledAssetsDir();

    /**
     * @brief Get the bundled chain params file used by the embedded node.
     * @return Path to params.yaml, or empty if unavailable
     */
    static QString bundledParamsPath();

    /**
     * @brief Get the bundled genesis file for a network.
     * @param network Network name (mainnet, testnet, devnet)
     * @return Path to the bundled genesis file, or empty if unavailable
     */
    static QString bundledGenesisPath(const QString& network);

    /**
     * @brief Ensure all required directories exist.
     * @return true if successful, false on error
     */
    static bool ensureDirectoriesExist();

private:
    AppPaths() = default; // Static class, no instances

    /**
     * @brief Create directory if it doesn't exist.
     * @param path Directory path
     * @return true if directory exists or was created
     */
    static bool ensureDir(const QString& path);
};

#endif // APPPATHS_H
