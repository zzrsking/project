"""UI 字体缓存。

为什么需要它：`pygame.font.Font(path, size)` 每次调用都要重新解析字体文件，
而旧代码在多个 `draw()` 里每帧创建字体（editor/gameplay/settings 面板都有），
一帧就是好几毫秒。字体是纯只读资源，按 (路径, 字号) 复用即可。

只依赖 pygame，不引入 settings/显示初始化，便于单独 import。
"""
import pygame

_FONT_CACHE = {}
DEFAULT_FONT_PATH = "汉仪润圆-75W.ttf"


def get_font(size, path=DEFAULT_FONT_PATH):
    """取一个缓存的 Font 实例。

    :param size: 字号
    :param path: 字体文件路径；传 None 表示 pygame 内置默认字体（与 `Font(None, size)` 等价）。
                 指定路径但文件缺失时回退到默认字体。
    """
    size = int(size)
    if size < 1:
        size = 1
    key = (path, size)
    font = _FONT_CACHE.get(key)
    if font is None:
        if path is None:
            font = pygame.font.Font(None, size)
        else:
            try:
                font = pygame.font.Font(path, size)
            except Exception:
                font = pygame.font.Font(None, size)
        _FONT_CACHE[key] = font
    return font


def clear_font_cache():
    _FONT_CACHE.clear()
