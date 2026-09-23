"""输入法（IME）辅助：让候选窗口跟随输入光标。

SDL2 通过 ``pygame.key.set_text_input_rect`` 接收"当前文本输入区域"，
输入法据此把候选框贴在光标附近。pygame 2.0+ 在 Windows / macOS / Linux
都提供该接口；缺失或调用失败时静默降级，不影响正常输入。

典型用法（输入框每帧或光标变动时调用一次 follow）::

    self._ime = ImeCursor()

    def focus(self):
        ...
        ime.start()

    def blur(self):
        ...
        ime.stop()
        self._ime.invalidate()

    def draw(self, surface, rect):
        ...
        # 光标在窗口坐标系里的位置
        self._ime.follow(cx, cy, w=1, h=rect.height)
"""
import pygame

# SDL 是否支持 set_text_input_rect（pygame 2.0+）
_HAS_IME_RECT = hasattr(pygame.key, "set_text_input_rect")


def supported():
    """当前 pygame/SDL 是否支持候选框跟随。"""
    return _HAS_IME_RECT


def start():
    """开启文本输入（激活 IME）。"""
    try:
        pygame.key.start_text_input()
    except Exception:
        pass


def stop():
    """关闭文本输入（收起 IME）。"""
    try:
        pygame.key.stop_text_input()
    except Exception:
        pass


def notify(x, y, w=1, h=1):
    """直接把光标矩形告诉 SDL（无缓存版本）。"""
    if not _HAS_IME_RECT:
        return
    try:
        pygame.key.set_text_input_rect(
            pygame.Rect(int(x), int(y), int(max(1, w)), int(max(1, h))))
    except Exception:
        pass


class ImeCursor:
    """跟踪一个输入框的光标位置；只有位置真的变了才调 SDL API。"""

    __slots__ = ("_last",)

    def __init__(self):
        self._last = None

    def invalidate(self):
        """标记位置失效，下次 follow() 必定重新通知 SDL。"""
        self._last = None

    def follow(self, x, y, w=1, h=1):
        """通知 SDL 当前光标矩形（窗口坐标）。

        位置与上次相同则跳过，避免每帧重复调 API。
        """
        if not _HAS_IME_RECT:
            return
        rect = (int(x), int(y), int(max(1, w)), int(max(1, h)))
        if rect == self._last:
            return
        self._last = rect
        try:
            pygame.key.set_text_input_rect(pygame.Rect(*rect))
        except Exception:
            # 通知失败就丢掉缓存，下次重试
            self._last = None
