#include "AddressBook.h"
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonArray>
#include <QJsonObject>
#include <QMap>
#include <QRegularExpression>
#include <QSaveFile>
#include <QSet>
#include <QTextStream>
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
    contact.note = json["note"].toString(json["notes"].toString());
    contact.createdAt = QDateTime::fromString(json["created_at"].toString(), Qt::ISODate);
    if (!contact.createdAt.isValid()) {
        contact.createdAt = QDateTime::currentDateTimeUtc();
    }
    return contact;
}

AddressBook::AddressBook(QObject* parent)
    : QObject(parent)
{
}

bool AddressBook::load(const QString& path)
{
    m_path = path;

    QList<Contact> contacts;
    QString error;
    if (!readContactsFile(path, &contacts, &error)) {
        qWarning() << "Failed to load address book:" << error;
        return false;
    }
    m_contacts = contacts;
    return true;
}

bool AddressBook::save(const QString& path)
{
    QString error;
    const bool ok = writeContactsFile(path, m_contacts, &error);
    if (!ok) {
        qWarning() << "Failed to save address book:" << error;
    }
    return ok;
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
            contact.address.contains(filter, Qt::CaseInsensitive) ||
            contact.note.contains(filter, Qt::CaseInsensitive)) {
            filtered.append(contact);
        }
    }
    return filtered;
}

AddressBook::ImportResult AddressBook::importFromFile(
    const QString& path,
    bool replaceExisting,
    const AddressValidator& validator
)
{
    ImportResult result;
    QList<Contact> importedContacts;
    if (!readContactsFile(path, &importedContacts, &result.error)) {
        return result;
    }

    QList<Contact> deduplicated;
    QSet<QString> seenInImport;
    for (Contact contact : importedContacts) {
        contact.label = contact.label.trimmed();
        contact.address = contact.address.trimmed();
        contact.note = contact.note.trimmed();
        if (contact.label.isEmpty() || contact.address.isEmpty()) {
            ++result.skipped;
            continue;
        }

        const QString normalizedAddress = contact.address;
        if (seenInImport.contains(normalizedAddress)) {
            ++result.skipped;
            continue;
        }

        const bool valid = validator ? validator(normalizedAddress) : validateAddress(normalizedAddress);
        if (!valid) {
            ++result.skipped;
            continue;
        }

        if (!contact.createdAt.isValid()) {
            contact.createdAt = QDateTime::currentDateTimeUtc();
        }

        seenInImport.insert(normalizedAddress);
        deduplicated.append(contact);
    }

    QList<Contact> merged = replaceExisting ? QList<Contact>() : m_contacts;
    QSet<QString> existingAddresses;
    for (const Contact& contact : merged) {
        existingAddresses.insert(contact.address);
    }

    for (const Contact& contact : deduplicated) {
        if (existingAddresses.contains(contact.address)) {
            ++result.skipped;
            continue;
        }
        merged.append(contact);
        existingAddresses.insert(contact.address);
        ++result.imported;
    }

    if (!replaceAllContacts(merged)) {
        result.error = "Failed to persist imported contacts.";
        result.imported = 0;
        result.skipped = 0;
        return result;
    }

    result.ok = true;
    return result;
}

AddressBook::ExportResult AddressBook::exportToFile(const QString& path) const
{
    ExportResult result;
    QString error;
    if (!writeContactsFile(path, m_contacts, &error)) {
        result.error = error;
        return result;
    }
    result.ok = true;
    result.exported = m_contacts.size();
    return result;
}

bool AddressBook::replaceAllContacts(const QList<Contact>& contacts)
{
    const QList<Contact> previous = m_contacts;
    m_contacts = contacts;
    if (!m_path.isEmpty() && !save(m_path)) {
        m_contacts = previous;
        return false;
    }

    QMap<QString, Contact> before;
    QMap<QString, Contact> after;
    for (const Contact& contact : previous) {
        before.insert(contact.address, contact);
    }
    for (const Contact& contact : m_contacts) {
        after.insert(contact.address, contact);
    }

    for (auto it = before.constBegin(); it != before.constEnd(); ++it) {
        if (!after.contains(it.key())) {
            emit contactRemoved(it.key());
        }
    }

    for (auto it = after.constBegin(); it != after.constEnd(); ++it) {
        if (!before.contains(it.key())) {
            emit contactAdded(it.value());
        } else {
            const Contact prior = before.value(it.key());
            const Contact current = it.value();
            if (prior.label != current.label || prior.note != current.note) {
                emit contactUpdated(current);
            }
        }
    }

    return true;
}

bool AddressBook::validateAddress(const QString& address) const
{
    const QString normalized = address.trimmed();
    if (!normalized.startsWith("anim1") || normalized.length() < 10 || normalized.length() > 128) {
        return false;
    }
    if (!normalized.contains(QRegularExpression("^anim1[qpzry9x8gf2tvdw0s3jn54khce6mua7l]+$"))) {
        return false;
    }
    return true;
}

bool AddressBook::readContactsFile(const QString& path, QList<Contact>* contacts, QString* error)
{
    contacts->clear();

    QFile file(path);
    if (!file.exists()) {
        return true;
    }
    if (!file.open(QIODevice::ReadOnly | QIODevice::Text)) {
        if (error) {
            *error = QString("Unable to open %1 for reading.").arg(path);
        }
        return false;
    }

    const QByteArray data = file.readAll();
    file.close();

    const QString suffix = QFileInfo(path).suffix().toLower();
    if (suffix == "csv") {
        QString text = QString::fromUtf8(data);
        QTextStream stream(&text, QIODevice::ReadOnly);
        bool firstRow = true;
        while (!stream.atEnd()) {
            const QString line = stream.readLine();
            if (line.trimmed().isEmpty()) {
                continue;
            }
            const QStringList row = parseCsvRow(line);
            if (firstRow && row.size() >= 3 &&
                row.value(0).compare("label", Qt::CaseInsensitive) == 0 &&
                row.value(1).compare("address", Qt::CaseInsensitive) == 0) {
                firstRow = false;
                continue;
            }
            firstRow = false;
            if (row.size() < 2) {
                continue;
            }
            Contact contact;
            contact.label = row.value(0).trimmed();
            contact.address = row.value(1).trimmed();
            contact.note = row.value(2).trimmed();
            contact.createdAt = QDateTime::fromString(row.value(3).trimmed(), Qt::ISODate);
            if (!contact.createdAt.isValid()) {
                contact.createdAt = QDateTime::currentDateTimeUtc();
            }
            contacts->append(contact);
        }
        return true;
    }

    QJsonParseError parseError;
    const QJsonDocument doc = QJsonDocument::fromJson(data, &parseError);
    if (parseError.error != QJsonParseError::NoError) {
        if (error) {
            *error = QString("Invalid JSON: %1").arg(parseError.errorString());
        }
        return false;
    }

    if (doc.isArray()) {
        *contacts = contactsFromJsonArray(doc.array());
        return true;
    }
    if (!doc.isObject()) {
        if (error) {
            *error = "Address book JSON must be an object or array.";
        }
        return false;
    }

    const QJsonObject root = doc.object();
    const QJsonArray contactArray = root.value("contacts").toArray(root.value("entries").toArray());
    *contacts = contactsFromJsonArray(contactArray);
    return true;
}

bool AddressBook::writeContactsFile(const QString& path, const QList<Contact>& contacts, QString* error)
{
    QDir parent = QFileInfo(path).absoluteDir();
    if (!parent.exists() && !parent.mkpath(".")) {
        if (error) {
            *error = QString("Unable to create %1.").arg(parent.absolutePath());
        }
        return false;
    }

    const QString suffix = QFileInfo(path).suffix().toLower();
    QSaveFile file(path);
    if (!file.open(QIODevice::WriteOnly | QIODevice::Text)) {
        if (error) {
            *error = QString("Unable to open %1 for writing.").arg(path);
        }
        return false;
    }

    if (suffix == "csv") {
        QTextStream stream(&file);
        stream << "label,address,note,created_at\n";
        for (const Contact& contact : contacts) {
            stream
                << csvField(contact.label) << ','
                << csvField(contact.address) << ','
                << csvField(contact.note) << ','
                << csvField(contact.createdAt.toUTC().toString(Qt::ISODate))
                << '\n';
        }
    } else {
        QJsonArray contactArray;
        for (const Contact& contact : contacts) {
            contactArray.append(contact.toJson());
        }
        QJsonObject root;
        root["version"] = 2;
        root["contacts"] = contactArray;
        const QByteArray json = QJsonDocument(root).toJson(QJsonDocument::Indented);
        if (file.write(json) != json.size()) {
            if (error) {
                *error = QString("Failed to write %1.").arg(path);
            }
            return false;
        }
    }

    if (!file.commit()) {
        if (error) {
            *error = QString("Failed to commit %1.").arg(path);
        }
        return false;
    }
    return true;
}

QList<Contact> AddressBook::contactsFromJsonArray(const QJsonArray& contactsArray)
{
    QList<Contact> contacts;
    for (const QJsonValue& value : contactsArray) {
        if (!value.isObject()) {
            continue;
        }
        contacts.append(Contact::fromJson(value.toObject()));
    }
    return contacts;
}

QStringList AddressBook::parseCsvRow(const QString& line)
{
    QStringList values;
    QString current;
    bool inQuotes = false;

    for (int i = 0; i < line.size(); ++i) {
        const QChar ch = line.at(i);
        if (ch == '"') {
            if (inQuotes && i + 1 < line.size() && line.at(i + 1) == '"') {
                current.append('"');
                ++i;
            } else {
                inQuotes = !inQuotes;
            }
        } else if (ch == ',' && !inQuotes) {
            values.append(current);
            current.clear();
        } else {
            current.append(ch);
        }
    }
    values.append(current);
    return values;
}

QString AddressBook::csvField(const QString& value)
{
    QString escaped = value;
    escaped.replace('"', "\"\"");
    return QString("\"%1\"").arg(escaped);
}
