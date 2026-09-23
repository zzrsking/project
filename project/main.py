import os
import sys

# ----------------------------------------------------------------------
# 路径引导：把「项目根」和「src」都加进 sys.path。
#
# 为什么需要：源码里同时存在两种 import 写法 ——
#   · 包内绝对导入 `from src.core.application import Application`  -> 需要【项目根】在 sys.path
#   · 顶层模块导入   `import serializer`（落在 src/serializer.py）、
#                    `from utils.helpers import ...`、`from game.camera import ...` -> 需要【src】在 sys.path
#
# PyCharm 的 source root 同时包含这两者（见 .idea/core.iml），所以 IDE 里能跑；
# 而直接 `python main.py` 时 sys.path 只有 main.py 所在目录，`import serializer` 会
# ModuleNotFoundError（并且原来的 try/except 只把异常打印成一行，很难看出原因）。
# 这里显式补齐，让 IDE 与命令行行为一致。
# ----------------------------------------------------------------------
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
for _path in (_PROJECT_ROOT, os.path.join(_PROJECT_ROOT, "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

# ----------------------------------------------------------------------
# IME 候选窗口：必须在 pygame.init()（即 SDL_Init）之前设置。
#
# SDL 在初始化输入法子系统时读取 SDL_IME_SHOW_UI 这个 hint，用来决定是否显示
# 候选窗口。原先写在 pygame.init() 之后，SDL 已经初始化完毕，hint 常常来不及
# 生效 —— 于是 set_text_input_rect() 调用完全正确，候选框却不出现。
# 这里提前到 import pygame 之前，保证 SDL 第一次初始化时就能读到。
# ----------------------------------------------------------------------
os.environ["SDL_IME_SHOW_UI"] = "1"

import pygame
try:
    from src.core.application import Application
except Exception as e:
    print(e)
    pygame.quit()
    sys.exit()
from settings import SCREEN_SIZE, VERSION

pygame.init()


def main():
    print('VERSION:', VERSION)
    engine = Application(SCREEN_SIZE)
    engine.run()

main()
