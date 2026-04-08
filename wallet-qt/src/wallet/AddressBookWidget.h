#ifndef ADDRESSBOOKWIDGET_H
#define ADDRESSBOOKWIDGET_H

#include "AddressBook.h"
#include <QWidget>
#include <QTableWidget>
#include <QPushButton>
#include <QLineEdit>

class WalletEngine;

/**
 * @brief Widget for managing address book contacts.
 * 
 * Features:
 * - Table view with label, address, note columns
 * - Add/Edit/Delete buttons
 * - Search/filter functionality
 * - Copy address button
 * - Address validation before save
 */
class AddressBookWidget : public QWidget
{
    Q_OBJECT

public:
    explicit AddressBookWidget(WalletEngine* engine, QWidget* parent = nullptr);
    
    /**
     * @brief Refresh contacts list from engine.
     */
    void refreshContacts();
    
    /**
     * @brief Get selected contact address.
     * @return Address or empty string if no selection
     */
    QString selectedContactAddress() const;

signals:
    void contactSelected(const QString& address);

private slots:
    void onAddClicked();
    void onEditClicked();
    void onDeleteClicked();
    void onCopyAddressClicked();
    void onImportClicked();
    void onExportClicked();
    void onSearchTextChanged(const QString& text);
    void onTableSelectionChanged();
    void onTableDoubleClicked(int row, int column);
    void handleContactAdded(const Contact& contact);
    void handleContactUpdated(const Contact& contact);
    void handleContactRemoved(const QString& address);

private:
    void setupUi();
    void updateContactRow(int row, const Contact& contact);
    void showAddEditDialog(const QString& existingAddress = QString());
    bool isOwnAddress(const QString& address) const;
    QString formatAddress(const QString& address) const;
    int findContactRow(const QString& address) const;
    
    WalletEngine* m_engine;
    QTableWidget* m_contactTable;
    QLineEdit* m_searchEdit;
    QPushButton* m_addButton;
    QPushButton* m_editButton;
    QPushButton* m_deleteButton;
    QPushButton* m_copyButton;
    QPushButton* m_importButton;
    QPushButton* m_exportButton;
};

#endif // ADDRESSBOOKWIDGET_H
