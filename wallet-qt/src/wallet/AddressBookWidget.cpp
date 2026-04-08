#include "AddressBookWidget.h"
#include "WalletEngine.h"
#include "WalletAccount.h"
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QHeaderView>
#include <QMessageBox>
#include <QDialog>
#include <QDialogButtonBox>
#include <QLabel>
#include <QFormLayout>
#include <QDir>
#include <QFileDialog>
#include <QFileInfo>
#include <QTextEdit>
#include <QClipboard>
#include <QApplication>

AddressBookWidget::AddressBookWidget(WalletEngine* engine, QWidget* parent)
    : QWidget(parent)
    , m_engine(engine)
{
    setupUi();
    
    // Connect to engine signals
    connect(m_engine, &WalletEngine::contactAdded, this, &AddressBookWidget::handleContactAdded);
    connect(m_engine, &WalletEngine::contactUpdated, this, &AddressBookWidget::handleContactUpdated);
    connect(m_engine, &WalletEngine::contactRemoved, this, &AddressBookWidget::handleContactRemoved);
    connect(m_engine, &WalletEngine::accountAdded, this, [this](const WalletAccount&) { refreshContacts(); });
    connect(m_engine, &WalletEngine::accountUpdated, this, [this](const WalletAccount&) { refreshContacts(); });
    connect(m_engine, &WalletEngine::accountRemoved, this, [this](const QString&) { refreshContacts(); });
}

void AddressBookWidget::setupUi()
{
    auto* layout = new QVBoxLayout(this);
    
    // Search bar
    auto* searchLayout = new QHBoxLayout();
    searchLayout->addWidget(new QLabel("Search:", this));
    m_searchEdit = new QLineEdit(this);
    m_searchEdit->setPlaceholderText("Filter by label or address...");
    searchLayout->addWidget(m_searchEdit);
    layout->addLayout(searchLayout);
    
    connect(m_searchEdit, &QLineEdit::textChanged, this, &AddressBookWidget::onSearchTextChanged);
    
    // Contacts table
    m_contactTable = new QTableWidget(0, 4, this);
    m_contactTable->setHorizontalHeaderLabels({"Label", "Address", "Type", "Note"});
    m_contactTable->horizontalHeader()->setStretchLastSection(true);
    m_contactTable->horizontalHeader()->setSectionResizeMode(1, QHeaderView::Stretch);
    m_contactTable->horizontalHeader()->setSectionResizeMode(2, QHeaderView::ResizeToContents);
    m_contactTable->setSelectionBehavior(QAbstractItemView::SelectRows);
    m_contactTable->setSelectionMode(QAbstractItemView::SingleSelection);
    m_contactTable->setEditTriggers(QAbstractItemView::NoEditTriggers);
    layout->addWidget(m_contactTable);
    
    connect(m_contactTable, &QTableWidget::doubleClicked,
            this, [this](const QModelIndex& index) { onTableDoubleClicked(index.row(), index.column()); });
    connect(m_contactTable, &QTableWidget::itemSelectionChanged,
            this, &AddressBookWidget::onTableSelectionChanged);
    
    // Action buttons
    auto* buttonLayout = new QHBoxLayout();
    m_addButton = new QPushButton("Add Contact", this);
    m_editButton = new QPushButton("Edit", this);
    m_deleteButton = new QPushButton("Delete", this);
    m_copyButton = new QPushButton("Copy Address", this);
    m_importButton = new QPushButton("Import", this);
    m_exportButton = new QPushButton("Export", this);
    
    m_editButton->setEnabled(false);
    m_deleteButton->setEnabled(false);
    m_copyButton->setEnabled(false);
    
    buttonLayout->addWidget(m_addButton);
    buttonLayout->addWidget(m_editButton);
    buttonLayout->addWidget(m_deleteButton);
    buttonLayout->addWidget(m_copyButton);
    buttonLayout->addWidget(m_importButton);
    buttonLayout->addWidget(m_exportButton);
    buttonLayout->addStretch();
    layout->addLayout(buttonLayout);
    
    connect(m_addButton, &QPushButton::clicked, this, &AddressBookWidget::onAddClicked);
    connect(m_editButton, &QPushButton::clicked, this, &AddressBookWidget::onEditClicked);
    connect(m_deleteButton, &QPushButton::clicked, this, &AddressBookWidget::onDeleteClicked);
    connect(m_copyButton, &QPushButton::clicked, this, &AddressBookWidget::onCopyAddressClicked);
    connect(m_importButton, &QPushButton::clicked, this, &AddressBookWidget::onImportClicked);
    connect(m_exportButton, &QPushButton::clicked, this, &AddressBookWidget::onExportClicked);
}

void AddressBookWidget::refreshContacts()
{
    m_contactTable->setRowCount(0);
    
    if (!m_engine) return;
    
    QString filter = m_searchEdit->text();
    auto contacts = m_engine->listContacts(filter);
    
    for (const auto& contact : contacts) {
        int row = m_contactTable->rowCount();
        m_contactTable->insertRow(row);
        updateContactRow(row, contact);
    }
}

void AddressBookWidget::updateContactRow(int row, const Contact& contact)
{
    auto* labelItem = new QTableWidgetItem(contact.label);
    labelItem->setData(Qt::UserRole, contact.address);
    labelItem->setToolTip(contact.label);
    m_contactTable->setItem(row, 0, labelItem);
    
    auto* addressItem = new QTableWidgetItem(formatAddress(contact.address));
    addressItem->setToolTip(contact.address);
    m_contactTable->setItem(row, 1, addressItem);
    m_contactTable->setItem(row, 2, new QTableWidgetItem(isOwnAddress(contact.address) ? "Own Address" : "Contact"));
    
    QString note = contact.note;
    if (note.length() > 50) {
        note = note.left(47) + "...";
    }
    auto* noteItem = new QTableWidgetItem(note);
    noteItem->setToolTip(contact.note);
    m_contactTable->setItem(row, 3, noteItem);
}

QString AddressBookWidget::formatAddress(const QString& address) const
{
    if (address.length() <= 20) {
        return address;
    }
    return address.left(12) + "..." + address.right(8);
}

int AddressBookWidget::findContactRow(const QString& address) const
{
    for (int i = 0; i < m_contactTable->rowCount(); ++i) {
        auto* item = m_contactTable->item(i, 0);
        if (item && item->data(Qt::UserRole).toString() == address) {
            return i;
        }
    }
    return -1;
}

QString AddressBookWidget::selectedContactAddress() const
{
    auto selected = m_contactTable->selectedItems();
    if (selected.isEmpty()) {
        return QString();
    }
    int row = selected.first()->row();
    auto* item = m_contactTable->item(row, 0);
    return item ? item->data(Qt::UserRole).toString() : QString();
}

void AddressBookWidget::showAddEditDialog(const QString& existingAddress)
{
    QDialog dialog(this);
    dialog.setWindowTitle(existingAddress.isEmpty() ? "Add Contact" : "Edit Contact");
    dialog.setMinimumWidth(400);
    
    auto* layout = new QVBoxLayout(&dialog);
    auto* formLayout = new QFormLayout();
    
    auto* labelEdit = new QLineEdit(&dialog);
    auto* addressEdit = new QLineEdit(&dialog);
    auto* noteEdit = new QTextEdit(&dialog);
    noteEdit->setMaximumHeight(80);
    
    // Load existing contact
    if (!existingAddress.isEmpty()) {
        auto contact = m_engine->listContacts().first();
        for (const auto& c : m_engine->listContacts()) {
            if (c.address == existingAddress) {
                contact = c;
                break;
            }
        }
        labelEdit->setText(contact.label);
        addressEdit->setText(contact.address);
        addressEdit->setReadOnly(true);
        noteEdit->setPlainText(contact.note);
    }
    
    formLayout->addRow("Label:", labelEdit);
    formLayout->addRow("Address:", addressEdit);
    formLayout->addRow("Note:", noteEdit);
    layout->addLayout(formLayout);
    
    auto* buttonBox = new QDialogButtonBox(QDialogButtonBox::Ok | QDialogButtonBox::Cancel, &dialog);
    connect(buttonBox, &QDialogButtonBox::accepted, &dialog, &QDialog::accept);
    connect(buttonBox, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
    layout->addWidget(buttonBox);
    
    if (dialog.exec() == QDialog::Accepted) {
        QString label = labelEdit->text().trimmed();
        QString address = addressEdit->text().trimmed();
        QString note = noteEdit->toPlainText().trimmed();
        
        if (label.isEmpty()) {
            QMessageBox::warning(this, "Error", "Label cannot be empty");
            return;
        }
        
        if (address.isEmpty()) {
            QMessageBox::warning(this, "Error", "Address cannot be empty");
            return;
        }
        
        if (!m_engine->validateAddress(address)) {
            QMessageBox::warning(this, "Error", "Invalid Animica address.");
            return;
        }
        
        bool success;
        if (existingAddress.isEmpty()) {
            success = m_engine->addContact(label, address, note);
        } else {
            success = m_engine->updateContact(address, label, note);
        }
        
        if (!success) {
            QMessageBox::warning(this, "Error", "Failed to save contact");
        }
    }
}

bool AddressBookWidget::isOwnAddress(const QString& address) const
{
    const QList<WalletAccount> accounts = m_engine->listAccounts();
    for (const WalletAccount& account : accounts) {
        if (account.address == address) {
            return true;
        }
    }
    return false;
}

void AddressBookWidget::onAddClicked()
{
    showAddEditDialog();
}

void AddressBookWidget::onEditClicked()
{
    QString address = selectedContactAddress();
    if (!address.isEmpty()) {
        showAddEditDialog(address);
    }
}

void AddressBookWidget::onDeleteClicked()
{
    QString address = selectedContactAddress();
    if (address.isEmpty()) return;
    
    auto reply = QMessageBox::question(this, "Delete Contact",
                                       "Delete this contact?",
                                       QMessageBox::Yes | QMessageBox::No);
    
    if (reply == QMessageBox::Yes) {
        if (!m_engine->removeContact(address)) {
            QMessageBox::warning(this, "Error", "Failed to delete contact");
        }
    }
}

void AddressBookWidget::onCopyAddressClicked()
{
    QString address = selectedContactAddress();
    if (!address.isEmpty()) {
        QApplication::clipboard()->setText(address);
    }
}

void AddressBookWidget::onImportClicked()
{
    const QString fileName = QFileDialog::getOpenFileName(
        this,
        "Import Contacts",
        QDir::homePath(),
        "Contacts (*.json *.csv)"
    );
    if (fileName.isEmpty()) {
        return;
    }

    QMessageBox choice(this);
    choice.setWindowTitle("Import Contacts");
    choice.setText("Import the selected contacts file into the local address book.");
    choice.setInformativeText("Choose Merge to keep existing contacts, or Replace to overwrite the local address book.");
    choice.addButton("Merge", QMessageBox::AcceptRole);
    QPushButton* replaceButton = choice.addButton("Replace", QMessageBox::DestructiveRole);
    QPushButton* cancelButton = choice.addButton(QMessageBox::Cancel);
    choice.exec();

    if (choice.clickedButton() == nullptr || choice.clickedButton() == cancelButton) {
        return;
    }

    const bool replaceExisting = choice.clickedButton() == replaceButton;
    const auto result = m_engine->importContactsFile(fileName, replaceExisting);
    if (!result.ok) {
        QMessageBox::warning(this, "Import Failed", result.error.isEmpty() ? "Failed to import contacts." : result.error);
        return;
    }

    refreshContacts();
    QMessageBox::information(
        this,
        "Contacts Imported",
        QString("Imported %1 contact(s); skipped %2.").arg(result.imported).arg(result.skipped)
    );
}

void AddressBookWidget::onExportClicked()
{
    const QString fileName = QFileDialog::getSaveFileName(
        this,
        "Export Contacts",
        QDir::home().filePath("animica-address-book.json"),
        "JSON Files (*.json);;CSV Files (*.csv)"
    );
    if (fileName.isEmpty()) {
        return;
    }

    QString destination = fileName;
    if (QFileInfo(destination).suffix().isEmpty()) {
        destination += ".json";
    }

    const auto result = m_engine->exportContactsFile(destination);
    if (!result.ok) {
        QMessageBox::warning(this, "Export Failed", result.error.isEmpty() ? "Failed to export contacts." : result.error);
        return;
    }

    QMessageBox::information(this, "Contacts Exported", QString("Exported %1 contact(s).").arg(result.exported));
}

void AddressBookWidget::onSearchTextChanged(const QString& text)
{
    Q_UNUSED(text);
    refreshContacts();
}

void AddressBookWidget::onTableSelectionChanged()
{
    bool hasSelection = !m_contactTable->selectedItems().isEmpty();
    m_editButton->setEnabled(hasSelection);
    m_deleteButton->setEnabled(hasSelection);
    m_copyButton->setEnabled(hasSelection);
    
    if (hasSelection) {
        emit contactSelected(selectedContactAddress());
    }
}

void AddressBookWidget::onTableDoubleClicked(int row, int column)
{
    Q_UNUSED(row);
    Q_UNUSED(column);
    onEditClicked();
}

void AddressBookWidget::handleContactAdded(const Contact& contact)
{
    int row = m_contactTable->rowCount();
    m_contactTable->insertRow(row);
    updateContactRow(row, contact);
}

void AddressBookWidget::handleContactUpdated(const Contact& contact)
{
    int row = findContactRow(contact.address);
    if (row >= 0) {
        updateContactRow(row, contact);
    }
}

void AddressBookWidget::handleContactRemoved(const QString& address)
{
    int row = findContactRow(address);
    if (row >= 0) {
        m_contactTable->removeRow(row);
    }
}
