"""UI 主题：编辑器 / 各处面板共用的配色、圆角与绘制工具。

为什么要有这个模块：button / switch / inputbox / scrollbar 已经统一到一套
"深色卡面 + 细描边 + 强调色" 的视觉，但 editor_ui、property_editor 里还是
散落的魔法数字（(28,28,38)、(70,70,90)、(42,42,55) ...），各写各的迟早跑偏。
集中到这里之后，改一处就能整体换肤。

只依赖 pygame，不引入 settings / 显示初始化，便于单独 import 与测试。
"""
import pygame

# ----------------------------------------------------------------------
# 背景层次：越靠上越亮
# 直接对齐【游戏内 UI】那套配色（dialogue_ui / inventory / settings_ui 用的就是这些）：
#   面板 40,40,50 · 描边 100,100,120 · 槽位 60,60,80 · 状态栏 50,50,70
#   选项悬停 80,80,120 · 强调文字 255,255,200 · 弹窗底 30,30,40
# 这样编辑器里外就是同一套颜色，不会两副面孔。
# ----------------------------------------------------------------------
BG_DEEP = (50, 50, 70)          # 最底 / 状态栏 / 弹窗底
BG_SURFACE = (40, 40, 50)       # 大面（= 游戏面板色）
CARD_BG = (50, 50, 70)          # 卡片 / 浮层（= 游戏状态栏色）
CARD_BG_HI = (60, 60, 80)       # 内嵌槽位（= 游戏槽位色）
BORDER = (100, 100, 120)        # 常规描边（= 游戏面板描边色）
BORDER_HI = (128, 128, 152)     # 悬停描边
SHADOW = (14, 14, 20)           # 投影颜色

# ----------------------------------------------------------------------
# 文本
# ----------------------------------------------------------------------
TEXT = (255, 255, 255)          # 主文本
TEXT_DIM = (200, 200, 200)      # 次级
TEXT_MUTED = (150, 150, 160)    # 弱化 / 提示

# ----------------------------------------------------------------------
# 强调色（同样取自游戏 UI）
# ----------------------------------------------------------------------
ACCENT = (80, 80, 120)          # 选中/悬停填充（= 游戏选项悬停色）
ACCENT_HI = (255, 255, 200)     # 强调高亮（= 游戏强调文字色）：指示条、光标等
ACCENT_SOFT = (150, 150, 200)   # 柔和版强调（描边、当前字段）
OK_GREEN = (80, 180, 80)        # = 游戏"提交"，也用作主按钮（Play / Editor）
CANCEL_RED = (180, 80, 80)      # = 游戏"取消"
PRIMARY = OK_GREEN              # 主按钮填充：借用游戏"提交"那颗绿，和次级按钮拉开主次

# ----------------------------------------------------------------------
# 几何
# ----------------------------------------------------------------------
RADIUS_CARD = 10
RADIUS_ROW = 7


def draw_card(surface, rect, radius=RADIUS_CARD, bg=CARD_BG, border=BORDER,
              shadow=10, shadow_alpha=95, tint_alpha=None):
    """圆角卡片 + 柔和投影。

    pygame 没有真正的模糊，这里用多层递减 alpha 的圆角矩形叠出投影感。
    :param tint_alpha: 不为 None 时卡片底色带透明度（用于压在游戏画面上的浮层）。
    """
    if shadow > 0:
        for i in range(shadow, 0, -2):
            alpha = int(shadow_alpha * (1.0 - i / shadow) ** 2)
            if alpha <= 0:
                continue
            layer = pygame.Surface((rect.width + i * 2, rect.height + i * 2),
                                   pygame.SRCALPHA)
            pygame.draw.rect(layer, (*SHADOW, alpha), layer.get_rect(),
                             border_radius=radius + i)
            surface.blit(layer, (rect.x - i, rect.y - i + 3))

    fill = (*bg[:3], 255 if tint_alpha is None else tint_alpha)
    pygame.draw.rect(surface, fill, rect, border_radius=radius)
    if border is not None:
        # 描边必须跟着 tint_alpha 一起淡：否则面板淡出时，卡片没了却剩一圈
        # 不透明的框留在屏幕上（"框消失了但还有字/线的残影"就是这么来的）
        edge = (*border[:3], 255 if tint_alpha is None else tint_alpha)
        pygame.draw.rect(surface, edge, rect, width=1, border_radius=radius)


def section_title(surface, text, pos, font, color=TEXT_DIM, accent=ACCENT):
    """小标题：左边一根强调色竖条 + 文本（比纯文字更有层次）。"""
    x, y = pos
    pygame.draw.rect(surface, accent, (x, y + 3, 3, 12), border_radius=2)
    surface.blit(font.render(text, True, color), (x + 11, y))


def chip(surface, text, pos, font, color=TEXT_DIM, bg=CARD_BG_HI,
         pad_x=7, pad_y=3, radius=RADIUS_ROW):
    """小标签（用于显示图层名之类的短信息）。返回占用的矩形。"""
    label = font.render(text, True, color)
    rect = pygame.Rect(pos[0], pos[1],
                       label.get_width() + pad_x * 2,
                       label.get_height() + pad_y * 2)
    pygame.draw.rect(surface, bg, rect, border_radius=radius)
    surface.blit(label, (rect.x + pad_x, rect.y + pad_y))
    return rect
