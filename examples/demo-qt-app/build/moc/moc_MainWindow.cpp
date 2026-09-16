// 合成 moc_MainWindow.cpp（仅用于 verify_moc 解析测试，非 Qt 真实产物逐字节拷贝）
// 方法清单与 examples/demo-qt-app/mainwindow.h 一致：
//   signals: processRequested, statusMessage
//   slots:   openSettings, onResultReady

#include <QtCore/qbytearray.h>
#include <QtCore/qmetatype.h>

struct qt_meta_stringdata_MainWindow_t {
    uint offsetsAndSizes[12];
    char stringdata0[12];
};

static const qt_meta_stringdata_MainWindow_t qt_meta_stringdata_MainWindow = {
    {
        QT_MOC_LITERAL(0, 0, 10) // "MainWindow"
        , QT_MOC_LITERAL(1, 11, 15) // "processRequested"
        , QT_MOC_LITERAL(2, 27, 13) // "statusMessage"
        , QT_MOC_LITERAL(3, 41, 12) // "openSettings"
        , QT_MOC_LITERAL(4, 54, 14) // "onResultReady"
        , QT_MOC_LITERAL(5, 69, 7) // "payload"
        , QT_MOC_LITERAL(6, 77, 3) // "msg"
        , QT_MOC_LITERAL(7, 81, 6) // "result"
    }
};

static const uint qt_meta_data_MainWindow[] = {
// content:
    8,       // revision
    0,       // classname
    0,    0, // classinfo
    4,   14, // methods
    0,    0, // properties
    0,    0, // enums/sets
    0,    0, // constructors
    0,       // flags
    2,       // signalCount

// signals: name, argc, parameters, tag, flags
    1,    1,   29,    2, 0x05, // processRequested
    2,    1,   32,    2, 0x05, // statusMessage

// slots: name, argc, parameters, tag, flags
    3,    0,   35,    2, 0x0a, // openSettings
    4,    1,   36,    2, 0x0a, // onResultReady

// signals parameters
    QMetaType::Void, QMetaType::QString,
    QMetaType::Void, QMetaType::QString,

// slots parameters
    QMetaType::Void,
    QMetaType::Void, QMetaType::QString,

    0        // eod
};

QT_BEGIN_MOC_NAMESPACE
QT_END_MOC_NAMESPACE
