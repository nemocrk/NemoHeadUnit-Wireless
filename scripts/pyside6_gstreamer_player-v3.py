import sys
import os
import ctypes
import ctypes.util
import gi

# Richiediamo le versioni corrette di GStreamer
gi.require_version('Gst', '1.0')
gi.require_version('GstGL', '1.0')
from gi.repository import Gst, GLib

from PySide6.QtCore import QObject, QUrl, Slot
from PySide6.QtGui import QGuiApplication, QSGRendererInterface
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from shiboken6 import Shiboken  # Necessario per passare il puntatore C++ nativo a PyGObject

# ==============================================================================
# RISOLUZIONE SCHERMO VUOTO - PUNTO 1: 
# Forziamo esplicitamente l'uso delle API OpenGL per lo Scene Graph di Qt6.
# GStreamer (attraverso qml6glsink) richiede la condivisione del contesto OpenGL/EGL.
# Se Qt6 utilizza Vulkan, Metal o Software rendering di default, lo schermo rimarrà vuoto.
# Questa istruzione DEVE essere chiamata prima di istanziare QGuiApplication.
# ==============================================================================
QQuickWindow.setGraphicsApi(QSGRendererInterface.OpenGL)

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

        // Elemento grafico nativo fornito da GStreamer qml6glsink.
        // Esegue il rendering direttamente nello Scene Graph RHI di Qt6
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
            onClicked: {
                console.log("QML: Cliccato Play");
                playerBridge.play();
            }
        }
        Button {
            text: "Pause"
            onClicked: {
                console.log("QML: Cliccato Pause");
                playerBridge.pause();
            }
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

    # ==============================================================================
    # RISOLUZIONE ERRORE 'is not a function' - PUNTO 2:
    # In PySide6, i metodi di una classe Python esposta a QML non sono richiamabili 
    # direttamente a meno che non vengano marcati esplicitamente con il decoratore @Slot().
    # Senza @Slot(), QML vede l'attributo ma non lo riconosce come funzione eseguibile.
    # ==============================================================================
    @Slot()
    def play(self):
        print("Riproduzione avviata...")
        self._pipeline.set_state(Gst.State.PLAYING)

    @Slot()
    def pause(self):
        print("Riproduzione in pausa...")
        self._pipeline.set_state(Gst.State.PAUSED)


def main():
    if len(sys.argv) < 2:
        print("Errore: specifica il percorso del file video.")
        print("Uso: python3 pyside6_gstreamer_player-v3.py /percorso/video.mp4")
        sys.exit(1)

    video_path = os.path.abspath(sys.argv[1])
    video_uri = QUrl.fromLocalFile(video_path).toString()

    # Inizializziamo l'applicazione Qt6
    app = QGuiApplication(sys.argv)
    engine = QQmlApplicationEngine()

    # Creiamo la pipeline GStreamer usando playbin
    pipeline = Gst.ElementFactory.make("playbin", "pipeline")
    sink = Gst.ElementFactory.make("qml6glsink", "qml6_sink")
    
    if not pipeline or not sink:
        print("Errore: Impossibile creare gli elementi GStreamer. Assicurati che 'qml6glsink' sia installato.")
        sys.exit(1)

    # Configuriamo playbin per usare il nostro qml6glsink personalizzato
    pipeline.set_property("video-sink", sink)
    pipeline.set_property("uri", video_uri)

    # Esponiamo i controlli a QML tramite la classe Bridge (lo facciamo prima di caricare il QML)
    bridge = PlayerBridge(pipeline, sink)
    engine.rootContext().setContextProperty("playerBridge", bridge)

    # Carichiamo la UI QML
    engine.loadData(QML_UI.encode('utf-8'))

    if not engine.rootObjects():
        sys.exit(-1)

    # Recuperiamo l'oggetto GstGLQt6VideoItem e la finestra principale
    root_object = engine.rootObjects()[0]
    video_item = root_object.findChild(QObject, "videoItem")
    window = root_object  # L'ApplicationWindow stessa eredita da QQuickWindow

    if not video_item:
        print("Errore: Impossibile trovare l'elemento videoItem nel file QML.")
        sys.exit(1)

    # ==============================================================================
    # RISOLUZIONE SCHERMO VUOTO - PUNTO 3 (TIMING):
    # Non possiamo impostare la proprietà 'widget' e avviare la pipeline immediatamente.
    # Dobbiamo attendere che Qt6 abbia effettivamente inizializzato il contesto grafico OpenGL (RHI)
    # sulla finestra. Questo momento esatto viene notificato tramite il segnale 'sceneGraphInitialized'.
    # Se passiamo il puntatore C++ prima di questo evento, GStreamer non troverà un contesto GL
    # valido a cui agganciarsi, lasciando lo schermo completamente nero o vuoto.
    # ==============================================================================
    def on_scenegraph_initialized():
        print("Scene Graph di Qt6 pronto! Procedo con l'aggancio del widget nativo...")
        try:
            # Otteniamo il puntatore C++ nativo dell'oggetto QML
            cpp_pointer = Shiboken.getCppPointer(video_item)[0]
            
            # Carichiamo dinamica di libgobject
            libgobject_path = ctypes.util.find_library('gobject-2.0')
            if not libgobject_path:
                libgobject_path = 'libgobject-2.0.so.0'
            libgobject = ctypes.CDLL(libgobject_path)
            
            # Definiamo la firma C di g_object_set
            libgobject.g_object_set.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p]
            libgobject.g_object_set.restype = None

            # Impostiamo in modo sicuro la proprietà gpointer "widget" bypassando PyGObject
            libgobject.g_object_set(hash(sink), b"widget", ctypes.c_void_p(cpp_pointer), None)
            print("[Ok] Puntatore widget nativo registrato su qml6glsink.")
        except Exception as e:
            print(f"[Warning] Impossibile usare ctypes: {e}. Tento fallback diretto...")
            sink.set_property("widget", Shiboken.getCppPointer(video_item)[0])

        # Solo ora che il contesto EGL/GL è perfettamente agganciato, attiviamo la riproduzione video!
        print("Avvio riproduzione video nella pipeline GStreamer...")
        pipeline.set_state(Gst.State.PLAYING)

    # Colleghiamo l'evento di inizializzazione grafica della finestra
    window.sceneGraphInitialized.connect(on_scenegraph_initialized)

    # Avviamo l'event loop di Qt
    exit_code = app.exec()

    # Rilasciamo le risorse di decodifica alla chiusura
    pipeline.set_state(Gst.State.NULL)
    sys.exit(exit_code)

if __name__ == "__main__":
    main()
