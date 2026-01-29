#ifndef CREATEACCOUNTDIALOG_H
#define CREATEACCOUNTDIALOG_H

#include <QDialog>
#include <QLineEdit>
#include <QComboBox>
#include <QLabel>
#include <QPushButton>
#include <QProgressBar>

class WalletEngine;

/**
 * @brief Dialog for creating a new wallet account.
 * 
 * Features:
 * - Label input with default suggestion (Account 1, Account 2, ...)
 * - Algorithm selection (Dilithium3 default, SPHINCS+ optional)
 * - Progress indicator during key generation
 * - Show generated address after creation
 */
class CreateAccountDialog : public QDialog
{
    Q_OBJECT

public:
    explicit CreateAccountDialog(WalletEngine* engine, QWidget* parent = nullptr);
    
    /**
     * @brief Get the created account label.
     */
    QString accountLabel() const;
    
    /**
     * @brief Get selected algorithm ID.
     */
    int algorithmId() const;
    
    /**
     * @brief Get generated address (after successful creation).
     */
    QString generatedAddress() const { return m_generatedAddress; }

private slots:
    void onCreateClicked();
    void onAccountCreated();

private:
    void setupUi();
    QString suggestLabel() const;
    
    WalletEngine* m_engine;
    QLineEdit* m_labelEdit;
    QComboBox* m_algorithmCombo;
    QLabel* m_progressLabel;
    QProgressBar* m_progressBar;
    QLabel* m_addressLabel;
    QPushButton* m_createButton;
    QPushButton* m_closeButton;
    QString m_generatedAddress;
};

#endif // CREATEACCOUNTDIALOG_H
