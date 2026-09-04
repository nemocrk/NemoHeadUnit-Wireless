import sys
import os
import ctypes
import ctypes.util
import gi

# 1. Forza le variabili d'ambiente critiche direttamente da Python prima di qualsiasi inizializzazione
os.environ["LIBVA_DRIVER_NAME"] = "i965"
# Forza GStreamer a usare le texture esterne OES se necessario su piattaforme embedded/Intel legacy
os.environ["QT_MULTIMEDIA_FORCE_GL_TEXTURE_EXTERNAL_OES"] = "1"

# Inizializza i binding GStreamer
gi.require_version('Gst', '1.0')
gi.require_version('GstGL', '1.0')
from gi.repository import Gst, GLib

from PySide6.QtCore import QObject, QUrl, Slot
from PySide6.QtGui import QGuiApplication, QSurfaceFormat
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface # Corretto posizionamento in QtQuick
from shiboken6 import Shiboken 

# Inizializziamo GStreamer
Gst.init(None)

# UI QML inline
QML_UI = """
import QtQuick 2.15
import QtQuick.Controls 2.15
import org.freedesktop.gstreamer.Qt6GLVideoItem 1.0

ApplicationWindow {
    id: window
    title: "Qt6 & GStreamer Wayland Zero-Copy Player"
    visible: true
    color: "black"

    GstGLQt6VideoItem {
        id: videoItem
        objectName: "videoItem"
        anchors.fill: parent
        smooth: true
        antialiasing: false
        opacity: 1.0
    }

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
    def __init__(self, pipeline):
        super().__init__()
        self._pipeline = pipeline

    @Slot() # Risolve l'errore "is not a function" in QML
    def play(self):
        print("Riproduzione avviata...")
        self._pipeline.set_state(Gst.State.PLAYING)

    @Slot() # Risolve l'errore "is not a function" in QML
    def pause(self):
        print("Riproduzione in pausa...")
        self._pipeline.set_state(Gst.State.PAUSED)
def on_eos(bus, msg, pipeline):
    print("Video terminato. Riavvio in loop...")
    # Esegue il seek alla posizione 0 (inizio del video)
    # FLUSH: svuota i buffer rimanenti
    # KEY_UNIT: salta al keyframe più vicino (molto più efficiente per l'hardware)
    pipeline.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, 0)

def main():
    if len(sys.argv) < 2:
        print("Errore: specifica il percorso del file video.")
        print("Uso: python3 pyside6_gstreamer_player-v4.py /percorso/video.mp4")
        sys.exit(1)

    video_path = os.path.abspath(sys.argv[1])
    if not os.path.exists(video_path):
        print(f"Errore: Il file {video_path} non esiste.")
        sys.exit(1)

    # Forza QtQuick a usare OpenGL RHI (indispensabile per qml6glsink con EGL)
    QQuickWindow.setGraphicsApi(QSGRendererInterface.OpenGL)

    format = QSurfaceFormat.defaultFormat()
    format.setAlphaBufferSize(0)
    QSurfaceFormat.setDefaultFormat(format)

    app = QGuiApplication(sys.argv)
    
    # Costruiamo la pipeline manuale esplicita che abbiamo dimostrato essere funzionante e zero-copy
    # Sostituiamo il generico 'playbin' (che falliva la negoziazione) con gli elementi corretti
    pipeline_str = (
        f"filesrc location=\"{video_path}\" ! "
        f"qtdemux ! h264parse ! vah264dec ! "
        f"vapostproc add-borders=true ! video/x-raw(memory:DMABuf),format=DMA_DRM,drm-format=YV12,width=1280,height=800 ! "
        f"glupload ! qml6glsink name=qml_sink"
    )
    print(f"Istanziando la pipeline: {pipeline_str}")
    pipeline = Gst.parse_launch(pipeline_str)
    sink = pipeline.get_by_name("qml_sink")
    if not pipeline or not sink:
        print("Errore: Impossibile creare la pipeline GStreamer o trovare 'qml6glsink'.")
        sys.exit(1)

    # --- NUOVO: Gestione del Bus per Loop e Errori ---
    bus = pipeline.get_bus()
    bus.add_signal_watch()  # Abilita l'emissione dei segnali GLib
    
    # Colleghiamo l'End of Stream alla funzione di loop
    bus.connect("message::eos", on_eos, pipeline)
    
    # Già che ci siamo, è buona prassi loggare eventuali errori hardware fatali
    def on_error(bus, msg):
        err, debug = msg.parse_error()
        print(f"Errore GStreamer: {err} - {debug}")
        app.quit()
        
    bus.connect("message::error", on_error)
    # -------------------------------------------------

    # Inizializziamo l'engine QML (GStreamer qml6glsink registrerà ora l'item grafico Qt6GLVideoItem)
    engine = QQmlApplicationEngine()
    engine.loadData(QML_UI.encode('utf-8'))

    if not engine.rootObjects():
        sys.exit(-1)

    root_object = engine.rootObjects()[0]
    video_item = root_object.findChild(QObject, "videoItem")
    window = qobject_cast_helper(root_object)

    if not video_item or not window:
        print("Errore: Impossibile trovare la finestra o l'elemento videoItem.")
        sys.exit(1)

    # Esponiamo il bridge a QML
    bridge = PlayerBridge(pipeline)
    engine.rootContext().setContextProperty("playerBridge", bridge)

    # Gestione del caricamento sicuro del widget a livello di contesti grafici
    def on_scenegraph_initialized():
        print("Scene Graph di Qt6 inizializzato. Aggancio del contesto video in corso...")
        try:
            # Ottiene il puntatore C++ nativo del widget QML
            cpp_pointer = Shiboken.getCppPointer(video_item)[0]
            
            # Carica libgobject per aggirare il blocco gpointer di PyGObject
            libgobject_path = ctypes.util.find_library('gobject-2.0') or 'libgobject-2.0.so.0'
            libgobject = ctypes.CDLL(libgobject_path)
            libgobject.g_object_set.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p]
            libgobject.g_object_set.restype = None

            # Imposta la proprietà 'widget' di qml6glsink via ctypes
            libgobject.g_object_set(hash(sink), b"widget", ctypes.c_void_p(cpp_pointer), None)
            print("[Ok] Widget agganciato con successo.")
            
            # Avvia la riproduzione
            pipeline.set_state(Gst.State.PLAYING)
        except Exception as e:
            print(f"Errore critico durante l'aggancio del widget: {e}")
            sys.exit(1)
    
    # Connette l'inizializzazione dello Scene Graph
    # 1. Prima controlla se per qualche motivo (es. caching) è GIÀ inizializzato
    if window.isSceneGraphInitialized():
        on_scenegraph_initialized()
    else:
        # 2. Altrimenti aggancia il segnale
        window.sceneGraphInitialized.connect(on_scenegraph_initialized)

    # 3. ORA forza la creazione dell'interfaccia grafica e del contesto EGL/OpenGL
    window.show()

    # Avvia l'event loop di Qt
    exit_code = app.exec()

    # Pulisce le risorse
    pipeline.set_state(Gst.State.NULL)
    sys.exit(exit_code)

def qobject_cast_helper(obj):
    # Helper per ottenere l'oggetto finestra principale convertito in QQuickWindow
    if isinstance(obj, QQuickWindow):
        return obj
    return None

if __name__ == "__main__":
    main()
