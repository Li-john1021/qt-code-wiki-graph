QT       += core gui widgets
TARGET    = DemoApp
TEMPLATE  = app
CONFIG   += c++17

SOURCES += \
    main.cpp \
    mainwindow.cpp \
    dataprocessor.cpp \
    workerthread.cpp \
    settingsdialog.cpp

HEADERS += \
    mainwindow.h \
    dataprocessor.h \
    workerthread.h \
    settingsdialog.h

FORMS += \
    mainwindow.ui
