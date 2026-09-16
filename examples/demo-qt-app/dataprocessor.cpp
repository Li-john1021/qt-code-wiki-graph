#include "dataprocessor.h"

DataProcessor::DataProcessor(QObject *parent)
    : QObject(parent)
{
}

void DataProcessor::process(const QString &payload)
{
    emit resultReady(transform(payload));
}

QString DataProcessor::transform(const QString &payload) const
{
    return QStringLiteral("OK:%1").arg(payload.trimmed().toUpper());
}
