#include "mainwindow.h"
#include "ui_mainwindow.h"
#include "dataprocessor.h"
#include "workerthread.h"
#include "settingsdialog.h"

#include <QMessageBox>

MainWindow::MainWindow(QWidget *parent)
    : QMainWindow(parent)
    , ui(new Ui::MainWindow)
    , m_processor(new DataProcessor(this))
    , m_settings(nullptr)
{
    ui->setupUi(this);
    setupConnections();
    setupWorker();
}

MainWindow::~MainWindow()
{
    if (m_workerThread) {
        m_workerThread->quit();
        m_workerThread->wait();
    }
    delete ui;
}

void MainWindow::setupConnections()
{
    // PMF 语法
    connect(ui->actionQuit, &QAction::triggered,
            this, &MainWindow::close);
    connect(ui->pushProcess, &QPushButton::clicked,
            this, [this]() {
                const QString payload = ui->lineEditPayload->text();
                emit processRequested(payload);
                emit statusMessage(tr("processing..."));
            });
    connect(m_processor, &DataProcessor::resultReady,
            this, &MainWindow::onResultReady);
    connect(this, &MainWindow::processRequested,
            m_processor, &DataProcessor::process);
    // 跨线程 QueuedConnection
    connect(this, &MainWindow::processRequested,
            m_workerThread, &WorkerThread::handleJob,
            Qt::QueuedConnection);
}

void MainWindow::setupWorker()
{
    m_workerThread = new WorkerThread(this);
    m_workerThread->start();
    connect(m_workerThread, &WorkerThread::jobDone,
            this, [this](const QString &note) {
                emit statusMessage(note);
            });
}

void MainWindow::openSettings()
{
    if (!m_settings)
        m_settings = new SettingsDialog(this);
    m_settings->show();
    m_settings->raise();
}

void MainWindow::onResultReady(const QString &result)
{
    ui->labelResult->setText(result);
    emit statusMessage(tr("done"));
}
