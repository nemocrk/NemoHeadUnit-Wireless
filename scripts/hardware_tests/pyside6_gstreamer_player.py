import sys
import os
import gi

# Richiediamo le versioni corrette di GStreamer
gi.require_version('Gst', '1.0')
gi.require_version('GstGL', '1.0')
from gi.repository import Gst, GLib

from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from shiboken6 import Shiboken # Necessario per passare il puntatore C++ nativo a PyGObject

# Inizializziamo GStreamer
Gst.init(None)

# QML UI definita inline per semplicità di esecuzione
QML_UI = """
import QtQuick 2.15
import QtQuick.Controls 2.15
// Importiamo l'elemento registrato dal plugin qml6glsink di GStreamer
import org.freedesktop.gstreamer.Qt6GLVideoItem 1.0

ApplicationWindow {
    id: window
    title: "Qt6 & GStreamer Wayland Zero-Copy Player"
    width: 800
    height: 480
    visible: true
    color: "black"

    Rectangle {
        id: videoContainer
        anchors.fill: parent
        color: "black"

        // Questo è l'elemento grafico nativo fornito da GStreamer qml6glsink.
        // Esegue il rendering direttamente nello Scene Graph RHI di Qt6 (OpenGL/Vulkan)
        GstGLQt6VideoItem {
            id: videoItem
            objectName: "videoItem"
            anchors.fill: parent
        }
    }

    // Overlay controlli semplice
    Row {
        anchors.bottom: parent.bottom
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.margins: 20
        spacing: 10

        Button {
            text: "Play"
            onClicked: playerBridge.play()
        }
        Button {
            text: "Pause"
            onClicked: playerBridge.pause()
        }
    }
}
"""

class PlayerBridge(QObject):
    """
    Classe Bridge per interfacciare l'interfaccia Qt6/PySide6 con la pipeline GStreamer.
    """
    def __init__(self, pipeline, sink):
        super().__init__()
        self._pipeline = pipeline
        self._sink = sink

    def play(self):
        print("Riproduzione avviata...")
        self._pipeline.set_state(Gst.State.PLAYING)

    def pause(self):
        print("Riproduzione in pausa...")
        self._pipeline.set_state(Gst.State.PAUSED)


def main():
    if len(sys.argv) < 2:
        print("Errore: specifica il percorso del file video.")
        print("Uso: python3 pyside6_gstreamer_player.py /percorso/video.mp4")
        sys.exit(1)

    video_path = os.path.abspath(sys.argv[1])
    video_uri = QUrl.fromLocalFile(video_path).toString()

    # Inizializziamo l'applicazione Qt6
    app = QGuiApplication(sys.argv)
    engine = QQmlApplicationEngine()

    # Creiamo la pipeline GStreamer usando playbin
    pipeline = Gst.ElementFactory.make("playbin", "pipeline")
    
    # Creiamo esplicitamente il sink video qml6glsink per l'integrazione Qt6
    sink = Gst.ElementFactory.make("qml6glsink", "qml6_sink")
    
    if not pipeline or not sink:
        print("Errore: Impossibile creare gli elementi GStreamer. Assicurati che 'qml6glsink' sia installato.")
        sys.exit(1)

    # Configuriamo playbin per usare il nostro qml6glsink personalizzato
    pipeline.set_property("video-sink", sink)
    pipeline.set_property("uri", video_uri)

    # Carichiamo la UI QML
    engine.loadData(QML_UI.encode('utf-8'))

    if not engine.rootObjects():
        sys.exit(-1)

    # Recuperiamo l'oggetto GstGLQt6VideoItem definito nel codice QML tramite objectName
    root_object = engine.rootObjects()[0]
    video_item = root_object.findChild(QObject, "videoItem")

    if not video_item:
        print("Errore: Impossibile trovare l'elemento videoItem nel file QML.")
        sys.exit(1)

    # CRITICO PER ZERO-COPY: Otteniamo il puntatore C++ nativo dell'oggetto QML
    # e lo passiamo al plugin GStreamer 'qml6glsink'. Questo consente a GStreamer
    # di condividere direttamente il contesto grafico (EGL/GL/Vulkan) con Qt6 RHI
    # senza copiare i frame in memoria CPU.
    cpp_pointer = Shiboken.getCppPointer(video_item)[0]
    sink.set_property("widget", cpp_pointer)

    # Esponiamo i controlli a QML tramite la classe Bridge
    bridge = PlayerBridge(pipeline, sink)
    engine.rootContext().setContextProperty("playerBridge", bridge)

    # Avviamo la riproduzione iniziale
    pipeline.set_state(Gst.State.PLAYING)

    # Avviamo l'event loop di Qt
    exit_code = app.exec()

    # Rilasciamo le risorse di decodifica alla chiusura
    pipeline.set_state(Gst.State.NULL)
    sys.exit(exit_code)

if __name__ == "__main__":
    main()
