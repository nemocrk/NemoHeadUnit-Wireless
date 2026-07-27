import ctypes
import logging
from multiprocessing import shared_memory
from PyQt6.QtGui import QImage

# Patch multiprocessing.resource_tracker to avoid false-positive leaked shared memory warnings
def patch_resource_tracker():
    from multiprocessing import resource_tracker
    
    orig_register = resource_tracker.register
    orig_unregister = resource_tracker.unregister

    def patched_register(name, rtype):
        if rtype == "shared_memory":
            return
        return orig_register(name, rtype)

    def patched_unregister(name, rtype):
        if rtype == "shared_memory":
            return
        return orig_unregister(name, rtype)

    resource_tracker.register = patched_register
    resource_tracker.unregister = patched_unregister

patch_resource_tracker()

logger = logging.getLogger("shared_buffer")

class DoubleSharedBuffer:
    def __init__(self, widget_id: str, max_width: int = 1920, max_height: int = 1080, create: bool = False):
        """
        Manages two shared memory buffers for double-buffering.
        
        :param widget_id: Unique string identifier for the widget process.
        :param max_width: Maximum physical width of the buffer.
        :param max_height: Maximum physical height of the buffer.
        :param create: If True, creates the segments (widget side). If False, attaches to existing ones (main window side).
        """
        self.widget_id = widget_id
        self.max_width = max_width
        self.max_height = max_height
        self.buffer_size = max_width * max_height * 4  # 4 bytes per pixel (ARGB32)
        self.create = create
        
        self.shm_names = [
            f"poc_shm_{widget_id}_buf_0",
            f"poc_shm_{widget_id}_buf_1"
        ]
        
        self.shms = []
        self.addresses = []
        
        try:
            for name in self.shm_names:
                if create:
                    # Clean up orphaned shared memory left over from previous crashes
                    try:
                        temp_shm = shared_memory.SharedMemory(name=name)
                        temp_shm.close()
                        temp_shm.unlink()
                        print(f"[shared_buffer] Cleaned up orphaned shared memory: {name}")
                    except FileNotFoundError:
                        pass
                    
                    shm = shared_memory.SharedMemory(name=name, create=True, size=self.buffer_size)
                else:
                    shm = shared_memory.SharedMemory(name=name)
                
                self.shms.append(shm)
                
                # Get the raw memory address of the shared memory buffer
                address = ctypes.addressof(ctypes.c_char.from_buffer(shm.buf))
                self.addresses.append(address)
                
        except Exception as e:
            print(f"[shared_buffer] Error initializing buffer: {e}")
            self.cleanup()
            raise e

    def get_image(self, index: int, logical_width: int, logical_height: int) -> QImage:
        """
        Returns a QImage wrapping the shared memory at the given index (0 or 1),
        configured with active logical dimensions and physical pitch.
        
        Modifying this QImage writes directly to the shared memory block (zero-copy).
        """
        if index < 0 or index >= len(self.addresses):
            raise IndexError("Buffer index out of range")
        
        # Ensure logical dimensions don't exceed physical max boundaries
        logical_width = min(logical_width, self.max_width)
        logical_height = min(logical_height, self.max_height)
        
        address = self.addresses[index]
        pitch = self.max_width * 4  # bytesPerLine must match physical width
        
        return QImage(address, logical_width, logical_height, pitch, QImage.Format.Format_ARGB32)

    def cleanup(self):
        """Closes and unlinks shared memory segments."""
        for shm in self.shms:
            try:
                shm.close()
            except Exception:
                pass
            if self.create:
                try:
                    shm.unlink()
                    print(f"[shared_buffer] Unlinked shared memory: {shm.name}")
                except Exception:
                    pass
        self.shms.clear()
        self.addresses.clear()
