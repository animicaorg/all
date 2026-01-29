#include "AddressBook.h"
#include <QFile>
#include <QJsonDocument>
#include <QJsonArray>
#include <QJsonObject>
#include <QProcess>
#include <QDebug>

QJsonObject Contact::toJson() const
{
    QJsonObject json;
    json["label"] = label;
    json["address"] = address;
    json["note"] = note;
    json["created_at"] = createdAt.toString(Qt::ISODate);
    return json;
}

Contact Contact::fromJson(const QJsonObject& json)
{
    Contact contact;
    contact.label = json["label"].toString();
    contact.address = json["address"].toString();
    contact.note = json["note"].toString();
    contact.createdAt = QDateTime::fromString(json["created_at"].toString(), Qt::ISODate);
    return contact;
}

AddressBook::AddressBook(QObject* parent)
    : QObject(parent)
{
}

bool AddressBook::load(const QString& path)
{
    m_path = path;
    
    QFile file(path);
    if (!file.exists()) {
        return true;
    }
    
    if (!file.open(QIODevice::ReadOnly)) {
        return false;
    }
    
    QByteArray data = file.readAll();
    file.close();
    
    QJsonDocument doc = QJsonDocument::fromJson(data);
    if (!doc.isObject()) {
        return false;
    }
    
    QJsonObject obj = doc.object();
    QJsonArray contacts = obj["contacts"].toArray();
    
    m_contacts.clear();
    for (const QJsonValue& val : contacts) {
        m_contacts.append(Contact::fromJson(val.toObject()));
    }
    
    return true;
}

bool AddressBook::save(const QString& path)
{
    QJsonArray contacts;
    for (const Contact& contact : m_contacts) {
        contacts.append(contact.toJson());
    }
    
    QJsonObject obj;
    obj["version"] = 1;
    obj["contacts"] = contacts;
    
    QJsonDocument doc(obj);
    QByteArray data = doc.toJson(QJsonDocument::Indented);
    
    QFile file(path);
    if (!file.open(QIODevice::WriteOnly)) {
        return false;
    }
    
    if (file.write(data) != data.size()) {
        file.close();
        return false;
    }
    
    file.close();
    return true;
}

bool AddressBook::addContact(const QString& label, const QString& address, const QString& note)
{
    if (!validateAddress(address)) {
        qWarning() << "Invalid address:" << address;
        return false;
    }
    
    // Check for duplicate
    for (const Contact& contact : m_contacts) {
        if (contact.address == address) {
            qWarning() << "Contact already exists:" << address;
            return false;
        }
    }
    
    Contact contact;
    contact.label = label;
    contact.address = address;
    contact.note = note;
    contact.createdAt = QDateTime::currentDateTimeUtc();
    
    m_contacts.append(contact);
    emit contactAdded(contact);
    
    if (!m_path.isEmpty()) {
        save(m_path);
    }
    
    return true;
}

bool AddressBook::updateContact(const QString& address, const QString& label, const QString& note)
{
    for (Contact& contact : m_contacts) {
        if (contact.address == address) {
            contact.label = label;
            contact.note = note;
            emit contactUpdated(contact);
            
            if (!m_path.isEmpty()) {
                save(m_path);
            }
            return true;
        }
    }
    return false;
}

bool AddressBook::removeContact(const QString& address)
{
    for (int i = 0; i < m_contacts.size(); ++i) {
        if (m_contacts[i].address == address) {
            m_contacts.removeAt(i);
            emit contactRemoved(address);
            
            if (!m_path.isEmpty()) {
                save(m_path);
            }
            return true;
        }
    }
    return false;
}

Contact AddressBook::getContact(const QString& address) const
{
    for (const Contact& contact : m_contacts) {
        if (contact.address == address) {
            return contact;
        }
    }
    return Contact();
}

QList<Contact> AddressBook::listContacts(const QString& filter) const
{
    if (filter.isEmpty()) {
        return m_contacts;
    }
    
    QList<Contact> filtered;
    for (const Contact& contact : m_contacts) {
        if (contact.label.contains(filter, Qt::CaseInsensitive) ||
            contact.address.contains(filter, Qt::CaseInsensitive)) {
            filtered.append(contact);
        }
    }
    return filtered;
}

bool AddressBook::validateAddress(const QString& address) const
{
    // Basic validation: must start with "anim1" and be reasonable length
    if (!address.startsWith("anim1") || address.length() < 10 || address.length() > 100) {
        return false;
    }
    
    // Use Python subprocess for full bech32m validation
    // python -c "from pq.py.address import validate_address; validate_address('anim1...'); print('valid')"
    QString pythonCode = QString(
        "from pq.py.address import validate_address; "
        "validate_address('%1'); "
        "print('valid')"
    ).arg(address);
    
    QProcess process;
    process.start("python", QStringList() << "-c" << pythonCode);
    
    if (!process.waitForStarted()) {
        qWarning() << "Failed to start Python address validation";
        return false;
    }
    
    if (!process.waitForFinished(5000)) {
        qWarning() << "Python address validation timeout";
        process.kill();
        return false;
    }
    
    return process.exitCode() == 0;
}
