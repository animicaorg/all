#ifndef DATADIRMANAGER_H
#define DATADIRMANAGER_H

#include <QString>
#include <QSettings>
#include <QObject>

/**
 * @brief Manages the Animica data directory configuration.
 * 
 * Responsibilities:
 * - Determine default data directory per OS
 * - Persist user-chosen data directory
 * - Support environment variable override
 * - Validate data directory health
 * - Manage network-specific subdirectories
 * 
 * Default paths by OS:
 * - macOS: ~/Library/Application Support/Animica/
 * - Windows: %APPDATA%\Animica\
 * - Linux: ~/.animica/ (backward compatible with existing node)
 * 
 * Directory structure:
 * <data_dir>/
 *   ├── wallets.json          # Wallet keystore
 *   ├── chain-1/              # Mainnet chain data
 *   ├── chain-2/              # Testnet chain data
 *   ├── chain-1337/           # Devnet chain data
 *   ├── logs/                 # Node logs
 *   ├── snapshots/            # Chain snapshots
 *   └── .network_id           # Network marker file
 */
class DataDirManager : public QObject
{
    Q_OBJECT

public:
    explicit DataDirManager(QObject* parent = nullptr);
    
    /**
     * @brief Get the currently configured data directory.
     * 
     * Priority order:
     * 1. Environment variable (ANIMICA_DATA_DIR)
     * 2. User-chosen path (from settings)
     * 3. OS-specific default
     * 
     * @return Absolute path to data directory
     */
    QString getDataDir() const;
    
    /**
     * @brief Set a custom data directory.
     * 
     * @param path Absolute path to use as data directory
     * @param validate If true, validates directory before setting
     * @return true if successfully set, false on validation error
     */
    bool setDataDir(const QString& path, bool validate = true);
    
    /**
     * @brief Get the default data directory for this OS.
     * 
     * @return OS-appropriate default path
     */
    static QString getDefaultDataDir();
    
    /**
     * @brief Get the wallets.json path in current data directory.
     * 
     * @return Full path to wallets.json
     */
    QString getWalletsFilePath() const;
    
    /**
     * @brief Get the chain data directory for a specific chain ID.
     * 
     * @param chainId Chain ID (1=mainnet, 2=testnet, 1337=devnet)
     * @return Full path to chain-specific directory
     */
    QString getChainDataDir(int chainId) const;
    
    /**
     * @brief Get the logs directory.
     * 
     * @return Full path to logs directory
     */
    QString getLogsDir() const;
    
    /**
     * @brief Get the snapshots directory.
     * 
     * @return Full path to snapshots directory
     */
    QString getSnapshotsDir() const;
    
    /**
     * @brief Check if data directory is valid and accessible.
     * 
     * Validates:
     * - Directory exists or can be created
     * - Directory is writable
     * - No permission issues
     * 
     * @param path Path to validate (uses current if empty)
     * @param errorMsg Output parameter for error message
     * @return true if valid
     */
    bool validateDataDir(const QString& path, QString& errorMsg) const;
    
    /**
     * @brief Ensure all required subdirectories exist in data directory.
     * 
     * Creates:
     * - Chain directories (chain-1, chain-2, chain-1337)
     * - logs/
     * - snapshots/
     * 
     * @return true if all directories exist or were created
     */
    bool ensureDirectoriesExist();
    
    /**
     * @brief Check if data directory has been initialized.
     * 
     * An initialized directory contains at least one of:
     * - wallets.json
     * - chain-* directory
     * - logs directory
     * 
     * @return true if directory appears to be in use
     */
    bool isDataDirInitialized() const;
    
    /**
     * @brief Get the network ID stored in the data directory.
     * 
     * Reads from .network_id marker file.
     * 
     * @return Network ID string, or empty if not set
     */
    QString getStoredNetworkId() const;
    
    /**
     * @brief Set the network ID marker in the data directory.
     * 
     * Creates/updates .network_id file.
     * 
     * @param networkId Network identifier (mainnet, testnet, devnet)
     * @return true if successfully written
     */
    bool setStoredNetworkId(const QString& networkId);
    
    /**
     * @brief Check if data directory network matches requested network.
     * 
     * @param requestedNetwork Network the user wants to use
     * @param errorMsg Output parameter for error message if mismatch
     * @return true if compatible
     */
    bool checkNetworkCompatibility(const QString& requestedNetwork, QString& errorMsg) const;

signals:
    /**
     * @brief Emitted when data directory changes.
     * 
     * @param newPath New data directory path
     */
    void dataDirChanged(const QString& newPath);

private:
    QSettings* m_settings;
    
    static constexpr const char* SETTINGS_KEY_DATA_DIR = "dataDir/customPath";
    static constexpr const char* ENV_VAR_DATA_DIR = "ANIMICA_DATA_DIR";
    static constexpr const char* NETWORK_MARKER_FILE = ".network_id";
};

#endif // DATADIRMANAGER_H
