#ifndef WALLETIMPORTER_H
#define WALLETIMPORTER_H

#include <QString>
#include <QJsonObject>
#include <QJsonArray>
#include <QObject>

/**
 * @brief Handles importing wallets.json files with validation and safety checks.
 * 
 * Import workflow:
 * 1. User selects a wallets.json file
 * 2. Validate JSON structure and schema
 * 3. Check for existing wallets.json in target directory
 * 4. If exists: prompt for Replace/Merge/Cancel
 * 5. Execute operation with atomic write and backup
 * 
 * Safety features:
 * - JSON schema validation
 * - Atomic write (temp file + fsync + rename)
 * - Timestamped backups
 * - Duplicate detection during merge
 * - Restrictive file permissions (Unix)
 */
class WalletImporter : public QObject
{
    Q_OBJECT

public:
    enum class ConflictResolution {
        Replace,  // Replace existing wallets.json entirely
        Merge,    // Merge with existing wallets (no duplicates)
        Cancel    // Don't import
    };
    Q_ENUM(ConflictResolution)
    
    struct ValidationResult {
        bool valid;
        QString errorMessage;
        int walletCount;
    };
    
    struct ImportResult {
        bool success;
        QString errorMessage;
        int walletsImported;
        int walletsSkipped;  // Duplicates during merge
        QString backupPath;  // Path to backup file if created
    };

    explicit WalletImporter(QObject* parent = nullptr);
    
    /**
     * @brief Validate a wallets.json file without importing it.
     * 
     * Checks:
     * - Valid JSON
     * - Has "version" field
     * - Has "wallets" array
     * - Each wallet has required fields (address, public_key_hex, secret_key_hex)
     * 
     * @param filePath Path to wallets.json file
     * @return Validation result
     */
    ValidationResult validateWalletFile(const QString& filePath) const;
    
    /**
     * @brief Import a wallets.json file to the target directory.
     * 
     * @param sourcePath Path to source wallets.json file
     * @param targetPath Path where wallets.json should be written
     * @param resolution How to handle conflicts with existing file
     * @return Import result
     */
    ImportResult importWallets(
        const QString& sourcePath,
        const QString& targetPath,
        ConflictResolution resolution
    );
    
    /**
     * @brief Check if a wallets.json file exists at the target path.
     * 
     * @param targetPath Path to check
     * @return true if file exists
     */
    static bool walletFileExists(const QString& targetPath);
    
    /**
     * @brief Create a timestamped backup of an existing wallets.json.
     * 
     * Backup filename format: wallets.json.bak.2026-01-29T12:34:56Z
     * 
     * @param walletPath Path to wallets.json
     * @return Path to backup file, or empty string on failure
     */
    QString createBackup(const QString& walletPath);

private:
    /**
     * @brief Load and parse a wallets.json file.
     * 
     * @param filePath Path to file
     * @param errorMsg Output parameter for error message
     * @return Parsed JSON object, or empty object on error
     */
    QJsonObject loadWalletFile(const QString& filePath, QString& errorMsg) const;
    
    /**
     * @brief Validate wallet JSON structure.
     * 
     * @param json Parsed JSON object
     * @param errorMsg Output parameter for error message
     * @return true if valid
     */
    bool validateWalletStructure(const QJsonObject& json, QString& errorMsg) const;
    
    /**
     * @brief Merge two wallet lists, removing duplicates.
     * 
     * Deduplication by address. Existing wallet data is preserved on conflict.
     * 
     * @param existing Existing wallets array
     * @param incoming Incoming wallets array
     * @param skipped Output parameter for number of duplicates skipped
     * @return Merged wallets array
     */
    QJsonArray mergeWallets(
        const QJsonArray& existing,
        const QJsonArray& incoming,
        int& skipped
    ) const;
    
    /**
     * @brief Write wallet JSON to file atomically.
     * 
     * Process:
     * 1. Write to temporary file (.tmp)
     * 2. Flush and fsync
     * 3. Rename to final path (atomic on POSIX)
     * 4. Set restrictive permissions
     * 
     * @param filePath Target path
     * @param json JSON object to write
     * @param errorMsg Output parameter for error message
     * @return true on success
     */
    bool writeWalletFileAtomic(
        const QString& filePath,
        const QJsonObject& json,
        QString& errorMsg
    ) const;
    
    /**
     * @brief Set restrictive file permissions (Unix).
     * 
     * On Unix-like systems: chmod 0600 (owner read/write only)
     * On Windows: best-effort ACL restrictions
     * 
     * @param filePath Path to file
     * @return true on success
     */
    bool setRestrictivePermissions(const QString& filePath) const;
};

#endif // WALLETIMPORTER_H
