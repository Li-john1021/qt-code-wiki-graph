#ifndef DATAPROCESSOR_H
#define DATAPROCESSOR_H

#include <QObject>

/// 同步业务处理器：接收 payload，产出结果字符串
class DataProcessor : public QObject
{
    Q_OBJECT
public:
    explicit DataProcessor(QObject *parent = nullptr);

signals:
    /// 处理完成
    void resultReady(const QString &result);

public slots:
    /// 处理一批 payload
    void process(const QString &payload);

private:
    QString transform(const QString &payload) const;
};

#endif // DATAPROCESSOR_H
