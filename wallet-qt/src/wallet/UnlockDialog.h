#ifndef UNLOCKDIALOG_H
#define UNLOCKDIALOG_H

#include <QDialog>
#include <QLineEdit>
#include <QLabel>
#include <QPushButton>
#include <QCheckBox>
#include <QSpinBox>

/**
 * @brief Dialog for unlocking the wallet.
 * 
 * Features:
 * - Password input with show/hide toggle
 * - Caps Lock warning indicator
 * - "Remember for session" checkbox
 * - Auto-lock timer configuration
 * - Rate limiting after failed attempts
 */
class UnlockDialog : public QDialog
{
    Q_OBJECT

public:
    explicit UnlockDialog(QWidget* parent = nullptr);
    
    /**
     * @brief Get the entered password.
     */
    QString password() const;
    
    /**
     * @brief Check if "remember for session" is checked.
     */
    bool rememberForSession() const;
    
    /**
     * @brief Get selected auto-lock timeout in minutes (0 = never).
     */
    int autoLockMinutes() const;
    
    /**
     * @brief Record a failed unlock attempt.
     * @return Delay in milliseconds before next attempt (0 if no delay)
     */
    int recordFailedAttempt();
    
    /**
     * @brief Reset failed attempt counter.
     */
    void resetFailedAttempts();
    
    /**
     * @brief Show error message in dialog.
     */
    void showError(const QString& message);

protected:
    void keyPressEvent(QKeyEvent* event) override;
    bool eventFilter(QObject* watched, QEvent* event) override;

private slots:
    void onShowPasswordToggled(bool checked);
    void onPasswordChanged(const QString& text);

private:
    void setupUi();
    void checkCapsLock();
    
    QLineEdit* m_passwordEdit;
    QLabel* m_capsLockLabel;
    QLabel* m_errorLabel;
    QPushButton* m_unlockButton;
    QPushButton* m_cancelButton;
    QCheckBox* m_showPasswordCheck;
    QCheckBox* m_rememberSessionCheck;
    QSpinBox* m_autoLockSpinBox;
    
    int m_failedAttempts;
    
    static constexpr int MAX_FREE_ATTEMPTS = 5;
};

#endif // UNLOCKDIALOG_H
