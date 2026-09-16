#ifndef SETTINGSDIALOG_H
#define SETTINGSDIALOG_H

#include <QDialog>

/// 简单设置对话框（无 .ui，演示非 UI 文件类）
class SettingsDialog : public QDialog
{
    Q_OBJECT
public:
    explicit SettingsDialog(QWidget *parent = nullptr);

signals:
    /// 设置已保存
    void settingsSaved();

public slots:
    /// 保存并关闭
    void saveAndClose();
};

#endif // SETTINGSDIALOG_H
