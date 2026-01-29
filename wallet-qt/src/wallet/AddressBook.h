#ifndef ADDRESSBOOK_H
#define ADDRESSBOOK_H

#include <QObject>
#include <QString>
#include <QDateTime>
#include <QList>
#include <QJsonObject>

/**
 * @brief Contact data structure.
 */
struct Contact {
    QString label;
    QString address;        // bech32m (anim1...)
    QString note;
    QDateTime createdAt;
    
    Contact() = default;
    
    QJsonObject toJson() const;
    static Contact fromJson(const QJsonObject& json);
};

/**
 * @brief Address book for managing contacts.
 * 
 * Stores contact information with bech32m address validation.
 * Persists to separate JSON file (unencrypted).
 */
class AddressBook : public QObject
{
    Q_OBJECT

public:
    explicit AddressBook(QObject* parent = nullptr);
    
    /**
     * @brief Load address book from file.
     * @param path Address book file path
     * @return true if successful
     */
    bool load(const QString& path);
    
    /**
     * @brief Save address book to file.
     * @param path Address book file path
     * @return true if successful
     */
    bool save(const QString& path);
    
    /**
     * @brief Add new contact.
     * @param label Contact name
     * @param address Bech32m address
     * @param note Optional note
     * @return true if added successfully
     */
    bool addContact(const QString& label, const QString& address, const QString& note = QString());
    
    /**
     * @brief Update existing contact.
     * @param address Contact address (key)
     * @param label New label
     * @param note New note
     * @return true if updated successfully
     */
    bool updateContact(const QString& address, const QString& label, const QString& note);
    
    /**
     * @brief Remove contact.
     * @param address Contact address
     * @return true if removed successfully
     */
    bool removeContact(const QString& address);
    
    /**
     * @brief Get contact by address.
     * @param address Contact address
     * @return Contact or invalid contact if not found
     */
    Contact getContact(const QString& address) const;
    
    /**
     * @brief List all contacts.
     * @param filter Optional filter string (matches label or address)
     * @return List of contacts
     */
    QList<Contact> listContacts(const QString& filter = QString()) const;
    
    /**
     * @brief Validate bech32m address.
     * @param address Address to validate
     * @return true if valid
     */
    bool validateAddress(const QString& address) const;
    
signals:
    void contactAdded(const Contact& contact);
    void contactUpdated(const Contact& contact);
    void contactRemoved(const QString& address);

private:
    QList<Contact> m_contacts;
    QString m_path;
};

#endif // ADDRESSBOOK_H
