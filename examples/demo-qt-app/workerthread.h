#ifndef WORKERTHREAD_H
#define WORKERTHREAD_H

#include <QThread>

/// 后台线程：接收 job，完成后发 jobDone
class WorkerThread : public QThread
{
    Q_OBJECT
public:
    explicit WorkerThread(QObject *parent = nullptr);

signals:
    /// job 完成通知（回主线程）
    void jobDone(const QString &note);

public slots:
    /// 在 worker 线程处理 job
    void handleJob(const QString &payload);

protected:
    void run() override;
};

#endif // WORKERTHREAD_H
