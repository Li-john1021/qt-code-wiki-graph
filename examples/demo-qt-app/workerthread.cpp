#include "workerthread.h"

#include <QThread>

WorkerThread::WorkerThread(QObject *parent)
    : QThread(parent)
{
}

void WorkerThread::handleJob(const QString &payload)
{
    QThread::msleep(10);
    emit jobDone(QStringLiteral("worker:%1").arg(payload));
}

void WorkerThread::run()
{
    exec();
}
