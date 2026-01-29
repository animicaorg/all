#include "WalletExporter.h"
#include <QFile>
#include <QFileInfo>
#include <QDebug>

WalletExporter::WalletExporter(QObject* parent)
    : QObject(parent)
{
}

WalletExporter::ExportResult WalletExporter::exportWallets(
    const QString& sourcePath,
    const QString& destinationPath,
    bool overwrite)
{
    ExportResult result{false, "", ""};
    
    // Validate source
    QString errorMsg;
    if (!canExport(sourcePath, errorMsg)) {
        result.errorMessage = errorMsg;
        return result;
    }
    
    // Check if destination exists
    if (QFile::exists(destinationPath) && !overwrite) {
        result.errorMessage = "Destination file already exists (use overwrite=true to replace)";
        return result;
    }
    
    // Remove destination if overwriting
    if (QFile::exists(destinationPath)) {
        if (!QFile::remove(destinationPath)) {
            result.errorMessage = "Cannot remove existing destination file";
            return result;
        }
    }
    
    // Copy file
    if (!QFile::copy(sourcePath, destinationPath)) {
        result.errorMessage = "Failed to copy wallet file";
        return result;
    }
    
    result.success = true;
    result.exportPath = destinationPath;
    
    qDebug() << "Exported wallets to:" << destinationPath;
    
    return result;
}

bool WalletExporter::canExport(const QString& sourcePath, QString& errorMsg) const
{
    // Check file exists
    if (!QFile::exists(sourcePath)) {
        errorMsg = "Source wallet file does not exist";
        return false;
    }
    
    // Check file is readable
    QFile file(sourcePath);
    if (!file.open(QIODevice::ReadOnly)) {
        errorMsg = "Source wallet file is not readable: " + file.errorString();
        return false;
    }
    file.close();
    
    return true;
}
