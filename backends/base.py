from PySide6.QtCore import QObject, Signal

class CanBackend(QObject):
    frame_received = Signal(object)
    status_changed = Signal(str)
    error_occurred = Signal(str)

    def start(self):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError
