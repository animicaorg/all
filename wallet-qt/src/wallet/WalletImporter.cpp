#include "WalletImporter.h"
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonArray>
#include <QJsonObject>
#include <QDateTime>
#include <QDebug>
#include <QSet>
#include <QDir>

#ifndef Q_OS_WIN
#include <unistd.h>
#endif

WalletImporter::WalletImporter(QObject* parent)
    : QObject(parent)
{
}

WalletImporter::ValidationResult WalletImporter::validateWalletFile(const QString& filePath) const
{
    ValidationResult result{false, "", 0};
    
    // Check file exists
    if (!QFile::exists(filePath)) {
        result.errorMessage = "File does not exist";
        return result;
    }
    
    // Load and parse JSON
    QString errorMsg;
    QJsonObject json = loadWalletFile(filePath, errorMsg);
    if (json.isEmpty()) {
        result.errorMessage = errorMsg;
        return result;
    }
    
    // Validate structure
    if (!validateWalletStructure(json, errorMsg)) {
        result.errorMessage = errorMsg;
        return result;
    }
    
    // Count wallets
    QJsonArray wallets = json["wallets"].toArray();
    result.walletCount = wallets.size();
    result.valid = true;
    
    return result;
}

WalletImporter::ImportResult WalletImporter::importWallets(
    const QString& sourcePath,
    const QString& targetPath,
    ConflictResolution resolution)
{
    ImportResult result{false, "", 0, 0, ""};
    
    // Validate source file
    ValidationResult validation = validateWalletFile(sourcePath);
    if (!validation.valid) {
        result.errorMessage = "Source file validation failed: " + validation.errorMessage;
        return result;
    }
    
    // Load source
    QString errorMsg;
    QJsonObject sourceJson = loadWalletFile(sourcePath, errorMsg);
    if (sourceJson.isEmpty()) {
        result.errorMessage = "Failed to load source: " + errorMsg;
        return result;
    }
    
    QJsonObject targetJson;
    
    // Handle existing file based on resolution
    if (walletFileExists(targetPath)) {
        if (resolution == ConflictResolution::Cancel) {
            result.errorMessage = "Import cancelled by user";
            return result;
        }
        
        if (resolution == ConflictResolution::Replace) {
            // Create backup before replacing
            QString backupPath = createBackup(targetPath);
            if (!backupPath.isEmpty()) {
                result.backupPath = backupPath;
                qDebug() << "Created backup:" << backupPath;
            }
            
            // Use source as-is
            targetJson = sourceJson;
            result.walletsImported = sourceJson["wallets"].toArray().size();
            
        } else if (resolution == ConflictResolution::Merge) {
            // Load existing and merge
            QJsonObject existingJson = loadWalletFile(targetPath, errorMsg);
            if (existingJson.isEmpty()) {
                result.errorMessage = "Failed to load existing file for merge: " + errorMsg;
                return result;
            }
            
            // Create backup before merging
            QString backupPath = createBackup(targetPath);
            if (!backupPath.isEmpty()) {
                result.backupPath = backupPath;
                qDebug() << "Created backup:" << backupPath;
            }
            
            // Merge wallet arrays
            QJsonArray existing = existingJson["wallets"].toArray();
            QJsonArray incoming = sourceJson["wallets"].toArray();
            int skipped = 0;
            QJsonArray merged = mergeWallets(existing, incoming, skipped);
            
            // Build target JSON
            targetJson = existingJson;  // Preserve other fields
            targetJson["wallets"] = merged;
            
            result.walletsImported = merged.size() - existing.size();
            result.walletsSkipped = skipped;
        }
    } else {
        // No existing file, just use source
        targetJson = sourceJson;
        result.walletsImported = sourceJson["wallets"].toArray().size();
    }
    
    // Write atomically
    if (!writeWalletFileAtomic(targetPath, targetJson, errorMsg)) {
        result.errorMessage = "Failed to write wallet file: " + errorMsg;
        return result;
    }
    
    result.success = true;
    qDebug() << "Import successful:" << result.walletsImported << "wallets imported,"
             << result.walletsSkipped << "duplicates skipped";
    
    return result;
}

bool WalletImporter::walletFileExists(const QString& targetPath)
{
    return QFile::exists(targetPath);
}

QString WalletImporter::createBackup(const QString& walletPath)
{
    if (!QFile::exists(walletPath)) {
        return QString();
    }
    
    // Generate timestamped backup filename
    QString timestamp = QDateTime::currentDateTimeUtc().toString("yyyy-MM-ddTHH:mm:ss");
    timestamp.replace(':', '-');  // Windows-safe
    QString backupPath = walletPath + ".bak." + timestamp + "Z";
    
    // Copy file
    if (!QFile::copy(walletPath, backupPath)) {
        qWarning() << "Failed to create backup:" << backupPath;
        return QString();
    }
    
    qDebug() << "Created backup:" << backupPath;
    return backupPath;
}

// Private methods

QJsonObject WalletImporter::loadWalletFile(const QString& filePath, QString& errorMsg) const
{
    QFile file(filePath);
    if (!file.open(QIODevice::ReadOnly | QIODevice::Text)) {
        errorMsg = "Cannot open file: " + file.errorString();
        return QJsonObject();
    }
    
    QByteArray data = file.readAll();
    file.close();
    
    QJsonParseError parseError;
    QJsonDocument doc = QJsonDocument::fromJson(data, &parseError);
    
    if (parseError.error != QJsonParseError::NoError) {
        errorMsg = "JSON parse error: " + parseError.errorString();
        return QJsonObject();
    }
    
    if (!doc.isObject()) {
        errorMsg = "Root element must be a JSON object";
        return QJsonObject();
    }
    
    return doc.object();
}

bool WalletImporter::validateWalletStructure(const QJsonObject& json, QString& errorMsg) const
{
    // Check version field
    if (!json.contains("version")) {
        errorMsg = "Missing 'version' field";
        return false;
    }
    
    // Check wallets array
    if (!json.contains("wallets")) {
        errorMsg = "Missing 'wallets' array";
        return false;
    }
    
    if (!json["wallets"].isArray()) {
        errorMsg = "'wallets' must be an array";
        return false;
    }
    
    QJsonArray wallets = json["wallets"].toArray();
    
    // Validate each wallet entry
    for (int i = 0; i < wallets.size(); ++i) {
        QJsonValue walletValue = wallets[i];
        if (!walletValue.isObject()) {
            errorMsg = QString("Wallet at index %1 is not an object").arg(i);
            return false;
        }
        
        QJsonObject wallet = walletValue.toObject();
        
        // Required fields
        QStringList requiredFields = {"address", "public_key_hex", "secret_key_hex"};
        for (const QString& field : requiredFields) {
            if (!wallet.contains(field)) {
                errorMsg = QString("Wallet at index %1 missing required field: %2").arg(i).arg(field);
                return false;
            }
            
            if (!wallet[field].isString() || wallet[field].toString().isEmpty()) {
                errorMsg = QString("Wallet at index %1 has invalid %2").arg(i).arg(field);
                return false;
            }
        }
    }
    
    return true;
}

QJsonArray WalletImporter::mergeWallets(
    const QJsonArray& existing,
    const QJsonArray& incoming,
    int& skipped) const
{
    skipped = 0;
    
    // Build set of existing addresses for quick lookup
    QSet<QString> existingAddresses;
    for (const QJsonValue& value : existing) {
        QJsonObject wallet = value.toObject();
        QString address = wallet["address"].toString().toLower();
        existingAddresses.insert(address);
    }
    
    // Start with existing wallets
    QJsonArray merged = existing;
    
    // Add incoming wallets that don't exist
    for (const QJsonValue& value : incoming) {
        QJsonObject wallet = value.toObject();
        QString address = wallet["address"].toString().toLower();
        
        if (existingAddresses.contains(address)) {
            // Duplicate - skip
            skipped++;
            qDebug() << "Skipping duplicate wallet:" << address;
        } else {
            // New wallet - add
            merged.append(value);
            existingAddresses.insert(address);
        }
    }
    
    return merged;
}

bool WalletImporter::writeWalletFileAtomic(
    const QString& filePath,
    const QJsonObject& json,
    QString& errorMsg) const
{
    // Create temp file in same directory (for atomic rename)
    QFileInfo info(filePath);
    QString tempPath = info.dir().filePath(".wallets.json.tmp");
    
    // Write to temp file
    QFile tempFile(tempPath);
    if (!tempFile.open(QIODevice::WriteOnly | QIODevice::Text | QIODevice::Truncate)) {
        errorMsg = "Cannot create temp file: " + tempFile.errorString();
        return false;
    }
    
    // Convert JSON to bytes
    QJsonDocument doc(json);
    QByteArray data = doc.toJson(QJsonDocument::Indented);
    
    // Write data
    qint64 written = tempFile.write(data);
    if (written != data.size()) {
        errorMsg = "Failed to write all data to temp file";
        tempFile.close();
        tempFile.remove();
        return false;
    }
    
    // Flush and sync
    tempFile.flush();
    
#ifndef Q_OS_WIN
    // fsync on Unix-like systems
    if (fsync(tempFile.handle()) != 0) {
        qWarning() << "fsync failed, continuing anyway";
    }
#endif
    
    tempFile.close();
    
    // Set restrictive permissions on temp file before rename
    setRestrictivePermissions(tempPath);
    
    // Atomic rename
    // Note: QFile::rename is atomic on POSIX systems
    if (QFile::exists(filePath)) {
        if (!QFile::remove(filePath)) {
            errorMsg = "Cannot remove existing file";
            QFile::remove(tempPath);
            return false;
        }
    }
    
    if (!QFile::rename(tempPath, filePath)) {
        errorMsg = "Cannot rename temp file to final path";
        QFile::remove(tempPath);
        return false;
    }
    
    // Ensure final permissions
    setRestrictivePermissions(filePath);
    
    qDebug() << "Wallet file written atomically:" << filePath;
    return true;
}

bool WalletImporter::setRestrictivePermissions(const QString& filePath) const
{
#ifndef Q_OS_WIN
    // Unix: Set 0600 (owner read/write only)
    QFile file(filePath);
    if (!file.setPermissions(QFile::ReadOwner | QFile::WriteOwner)) {
        qWarning() << "Failed to set restrictive permissions on:" << filePath;
        return false;
    }
    qDebug() << "Set permissions 0600 on:" << filePath;
    return true;
#else
    // Windows: Best-effort - QFile permissions are limited on Windows
    // TODO: Add Windows ACL restrictions if needed
    qDebug() << "Skipping permission restriction on Windows:" << filePath;
    return true;
#endif
}
