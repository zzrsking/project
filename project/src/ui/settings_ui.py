import pygame
import threading
from src.ui.elements.button import UIButton
from src.ui.elements.fonts import get_font
import settings



class SettingsInterface:
    def __init__(self, screen_rect):
        self.panel = screen_rect
        self.mode_buttons = {
            "保存进度":UIButton(pygame.Rect(150, 230-70, 150, 40), "保存进度", (60,180,60)),
            "加载进度":UIButton(pygame.Rect(150, 290-70, 150, 40), "加载进度", (60,60,180)),
            "返回":UIButton(pygame.Rect(150, 350-70, 150, 40), "返回", (180,60,60)),
            "test":UIButton(pygame.Rect(150, 410-70, 150, 40), "test", (255, 200, 0)),
        }
        self._reg_switch = {"DPM":settings.DISPLAY_PORT_MARKERS,
                            "DEPTH":settings.DISPLAY_DEPTH,
                            "DAB":settings.DISPLAY_ACTIVE_BLOCKS,
                            "DSCP": settings.DISPLAY_SCREEN_CENTER_POINT,
                            "PFR":settings.PROFILE_RENDER,
                            "RSC":settings.RENDER_STATIC_CACHE,
                            "OCF":settings.OCCLUSION_FADE}
        self.switch_theme = {"bg_off": (60, 60, 80), "bg_on": (30, 200, 90), "knob": (220, 220, 230)}

    def save_func(self):
        pass
    def load_func(self):
        pass

    def _panel_surface(self):
        """缓存的设置面板背景（尺寸/颜色不变，无需每帧重建）。"""
        surf = getattr(self, "_panel_cache", None)
        if surf is None or surf.get_size() != self.panel.size:
            surf = pygame.Surface(self.panel.size, pygame.SRCALPHA)
            surf.fill((40, 40, 50, 220))
            self._panel_cache = surf
        return surf

    def draw(self, surface, mouse_pos):
        # 背景板是常量：只做一次（旧实现每帧新建 1200x800 的 SRCALPHA 表面再填充，
        # 一帧就是 3.8MB 的内存分配 + 一次全屏混合）
        panel = self._panel_surface()
        surface.blit(panel, self.panel.topleft)
        font = get_font(32)
        title = font.render("游戏设置", True, (255,255,255))
        surface.blit(title, (self.panel.x + 150, self.panel.y + 20))
        for btn in self.mode_buttons.values():
            btn.check_hover(mouse_pos)
            btn.draw(surface)
        for name, sw, rect in _iter_switch_and_rect(self._reg_switch):
            sw.set_hover(rect.collidepoint(mouse_pos))
            sw.draw(surface, rect,
                                          bg_off=self.switch_theme["bg_off"],
                                          bg_on=self.switch_theme["bg_on"],
                                          knob_color=self.switch_theme["knob"],
                                          knob_hover_color=(255, 255, 255))
            txt_rect = rect.copy()
            txt_rect.x -= 30 + txt_rect.width
            txt = font.render(name, True, (255,255,255))
            surface.blit(txt, txt_rect)

    def handle_event(self, event):
        if event.type == pygame.MOUSEBUTTONDOWN:
            if event.button == 1:
                if self.mode_buttons["保存进度"].rect.collidepoint(event.pos):
                    self.save_func()
                elif self.mode_buttons["加载进度"].rect.collidepoint(event.pos):
                    self.load_func()
                elif self.mode_buttons["test"].rect.collidepoint(event.pos):
                    ...
            if event.button == 1:
                pos = event.pos
                for name, sw, rect in _iter_switch_and_rect(self._reg_switch):
                    if rect.collidepoint(pos):
                        sw.switch()

    def update(self, mouse_pos, dt):
        for sw in self._reg_switch.values():
            sw.update(dt)


def _iter_switch_and_rect(switch_dict:dict):
    """产出 (名字, Switch 实例, 矩形)。旧版只产出名字/矩形，调用方还要用 dict.get(name)
    反查一遍实例 —— 现在直接把实例给出去，省掉每帧的字典查找。"""
    for inx, (name, switch) in enumerate(switch_dict.items(), 1):
        yield name, switch, pygame.Rect(settings.SCREEN_SIZE[0]-200, inx*60, 120, 45)