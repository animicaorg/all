#ifndef WALLETEXPORTER_H
#define WALLETEXPORTER_H

#include <QString>
#include <QObject>

/**
 * @brief Handles exporting wallets.json files.
 * 
 * Export workflow:
 * 1. User selects destination folder/file
 * 2. Copy current wallets.json to destination
 * 3. Optionally create a ZIP archive with backups
 * 
 * Safety features:
 * - Validates source file exists
 * - Checks destination is writable
 * - Provides warning about sensitive data
 */
class WalletExporter : public QObject
{
    Q_OBJECT

public:
    struct ExportResult {
        bool success;
        QString errorMessage;
        QString exportPath;
    };

    explicit WalletExporter(QObject* parent = nullptr);
    
    /**
     * @brief Export wallets.json to a destination path.
     * 
     * @param sourcePath Path to current wallets.json
     * @param destinationPath Path where file should be exported
     * @param overwrite If true, overwrite existing file
     * @return Export result
     */
    ExportResult exportWallets(
        const QString& sourcePath,
        const QString& destinationPath,
        bool overwrite = false
    );
    
    /**
     * @brief Check if a wallet file can be exported.
     * 
     * @param sourcePath Path to wallets.json
     * @param errorMsg Output parameter for error message
     * @return true if file exists and is readable
     */
    bool canExport(const QString& sourcePath, QString& errorMsg) const;
};

#endif // WALLETEXPORTER_H
