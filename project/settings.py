import os
import pygame

from src.physics.physics_world import PhysicsWorld
from src.core.command import Command
from src.game.camera import Camera
from src.ui.elements.switch import Switch

VERSION = "1.3.9"

# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


# 屏幕设置
SCREEN_SIZE = (1200, 800)

ACTIVE_ZONE_SIZE = SCREEN_SIZE
# 渲染后端选择：'pygame' 或 'opengl'
RENDER_BACKEND = 'opengl'   # 改为 'pygame' 可切换回CPU渲染

def _create_display(size):
    """建立显示窗口。优先 OpenGL（opengl 渲染后端需要它）。

    没有 GL 可用时（headless / CI / SDL dummy 驱动）**回落到普通 Surface**，
    而不是让 import settings 直接抛 pygame.error。这一点很重要：structure.py 经
    utils.helpers 会 import settings，一旦这里抛错，**所有结构级的无显示测试
    全部 import 不进来**（checklist 测试也就失去护栏作用，只能靠肉眼在画面里
    找“端口画不出来”“深度错乱”这类不报错的问题）。
    带 GL 的真实机器上行为与以前完全一致（仍然 OPENGL + DOUBLEBUF）。
    """
    try:
        return pygame.display.set_mode(size, pygame.OPENGL | pygame.DOUBLEBUF), True
    except pygame.error:
        return pygame.display.set_mode(size), False


_display, GL_AVAILABLE = _create_display(SCREEN_SIZE)
pygame.display.set_caption("Engine Run!!!")

# 资源路径
ASSETS_PATH = os.path.join(PROJECT_ROOT, 'assets')

# 碰撞编辑器开关
COLLISION_EDITOR_ENABLED = True # nowhere to use ?!

DISPLAY_ACTIVE_BLOCKS = Switch(False)
Command.register(DISPLAY_ACTIVE_BLOCKS, name="DAB")

DISPLAY_SCREEN_CENTER_POINT = Switch(True)
Command.register(DISPLAY_SCREEN_CENTER_POINT, name="DSCP")

# 端口( Port )调试特效开关：开启时由渲染器叠加 绿=空闲/黄=已连接 半透明标记。
# 这只是渲染特效 —— 不修改任何精灵的 image，也不让 Structure 参与绘制。
DISPLAY_PORT_MARKERS = Switch(False)
Command.register(DISPLAY_PORT_MARKERS, name="DPM")

# 深度调试开关：开启后叠加每个对象的"排序深度"框
#   洋红框 = 该对象的绘制范围；青色横线 = 它的 depth 基准线；文字 = depth 数值。
# 用来肉眼核对 depth / layer_offset 的排序是否符合预期。
DISPLAY_DEPTH = Switch(False)
Command.register(DISPLAY_DEPTH, name="DEPTH")

# 渲染性能剖析：开启后 OpenGL 渲染器按阶段累计耗时并周期性打印（定位瓶颈用）
PROFILE_RENDER = Switch(False)
Command.register(PROFILE_RENDER, name="PFR")

# 静态地形缓冲缓存：开=分桶缓存路径(快)；关=退回逐帧全扫的旧路径(慢但基准)。
# 用来 A/B 对比画面是否一致，验证缓存算法正确性。
RENDER_STATIC_CACHE = Switch(True)
Command.register(RENDER_STATIC_CACHE, name="RSC")


grid_size = 64

physics = PhysicsWorld(gravity=(0,0*grid_size))


# =====================================================================
# Layer v2 —— 特殊层配置（LAYER_SYSTEM_V2_DESIGN.md §2-决策5 / §4）
# default 与 test 各自独立、可分别开关，互不耦合。由启动装配时按此建立，
# 不再写死在 GameWorld 里，也禁止运行时静默隐式建层。
#   - default: 兜底层。存在 => add 遇到未知层名宽松回落 default；
#              不存在 => add 遇到未知层名直接抛 UnknownLayerError。
#   - test   : 测试/调试专用层，正式对象不应落进去。
# =====================================================================
ENABLE_DEFAULT_LAYER = Switch(True)
Command.register(ENABLE_DEFAULT_LAYER, name="EDL")   # 兜底层(default)是否建立
ENABLE_TEST_LAYER = Switch(True)
Command.register(ENABLE_TEST_LAYER, name="ETL")      # 测试专用层(test)是否建立

# 键 = 层名，值 = LayerSpec 的字段（kind 用字符串，见 LayerKind）
LAYER_DEFAULTS = {
    "default": {"kind": "object", "order": 1000, "synthetic": True, "y_sorted": False},
    "test":    {"kind": "object", "order": 1001, "synthetic": True, "y_sorted": False},
}


# =====================================================================
# 深度排序锚点（Y 轴取对象的哪个 Y 参与前后排序）
#   - 'bottom' : 对象包围盒【下缘】（最底部的那个 Y）—— 引擎默认。
#                角色/物体以“脚底”排序，站在同一地面线上的次序才自然。
#   - 'center' : 对象中心 Y（旧行为，保留作 A/B 对比）。
# 开关只是实验性对比用；房屋 Structure 内部的遮挡顺序（前墙/后墙如何切）
# 仍待商榷 —— 详见 layer_v2.depth_y 与 house._iter_sprites_with_depth。
# =====================================================================
DEPTH_ANCHOR = "bottom"


# =====================================================================
# 竖墙侧面厚度（目标3）：墙的贴图实测只占半格 32px、另一半是空的，
# 填上侧脸纹之后墙就有了可见厚度（前后有致的 2.5D 纵深感）。
#
#   None  = 关（当前值）。竖墙贴图只画半格是【有意的侧面设计】，不是缺陷：
#           规划中是"上窄下宽"——上面半格束口、下面全格。所以默认必须保持空半格。
#   其他值（"44"/"49"…）只是在你想试"填满"时的实验开关，默认不要开。
# =====================================================================
WALL_SIDE_FACE = None

WALL_SIDE_FACE_FILL = 1.0


# =====================================================================
# 遮挡淡入淡出：谁挡住玩家，谁就淡到半透明，让玩家看见里面。
#
# 判定是【通用规则】，不含任何结构特例：一个单位被算作"遮挡者"要同时满足
#   ① 绘制顺序排在焦点之后（更近，后画 = 盖在上面）
#   ② 与焦点的【实际绘制范围】像素相交（只更近不相交，对画面毫无影响）
# 粒度是【宿主结构】：一面墙的叶子必须一起淡，否则同一面墙有的淡有的不淡像破图。
#
# 想让它永不淡出的结构，在它自己身上写 occlusion_policy = OcclusionPolicy.NONE。
# =====================================================================
OCCLUSION_FADE = Switch(True)
Command.register(OCCLUSION_FADE, name="OCF")          # 总开关
OCCLUSION_FADE_ALPHA = 0.6    # 淡出的目标不透明度（0=全消失，1=等于不淡）
OCCLUSION_FADE_SPEED = 5.0     # 每秒不透明度变化量（越大淡得越快，5.0 约 0.13 秒到底）


main_camera = Camera(smoothness=4.0)
free_camera = Camera(smoothness=4.0)


