#ifndef MAINWINDOW_H
#define MAINWINDOW_H

#include <QMainWindow>
#include <QThread>

QT_BEGIN_NAMESPACE
namespace Ui { class MainWindow; }
QT_END_NAMESPACE

class DataProcessor;
class WorkerThread;
class SettingsDialog;

/// 主窗口：装配 UI、业务处理器与后台 Worker
class MainWindow : public QMainWindow
{
    Q_OBJECT
public:
    explicit MainWindow(QWidget *parent = nullptr);
    ~MainWindow();

signals:
    /// 请求开始处理一批数据
    void processRequested(const QString &payload);
    /// 状态栏提示
    void statusMessage(const QString &msg);

public slots:
    /// 打开设置对话框
    void openSettings();
    /// 接收处理完成结果并刷新 UI
    void onResultReady(const QString &result);

private:
    void setupConnections();
    void setupWorker();

    Ui::MainWindow *ui;
    DataProcessor *m_processor;
    WorkerThread *m_workerThread;
    SettingsDialog *m_settings;
};

#endif // MAINWINDOW_H
