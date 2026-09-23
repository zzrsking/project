from enum import Enum

class GameMode(Enum):
    PLAY = (1, True)
    EDIT = (2, False)
    SETTINGS = (3, False)
    DIALOGUE = (4, False)

    def __init__(self, value, camera_lock):
        self._value_ = value
        self.camera_lock = camera_lock
