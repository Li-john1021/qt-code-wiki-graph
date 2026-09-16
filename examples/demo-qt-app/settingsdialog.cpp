#include "settingsdialog.h"

#include <QVBoxLayout>
#include <QPushButton>

SettingsDialog::SettingsDialog(QWidget *parent)
    : QDialog(parent)
{
    auto *lay = new QVBoxLayout(this);
    auto *btn = new QPushButton(tr("Save"), this);
    lay->addWidget(btn);
    connect(btn, &QPushButton::clicked, this, &SettingsDialog::saveAndClose);
}

void SettingsDialog::saveAndClose()
{
    emit settingsSaved();
    accept();
}
