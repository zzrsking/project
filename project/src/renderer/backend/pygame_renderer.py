import pygame
import math
from pygame import Vector2
from src.core.state import GameMode
from src.game.entity import Tiled
from src.utils.layer_v2 import (LayerKind, leaf_sprites_of,
                                depth_sorted_draw_units)
import settings

class PygameRenderer:
    def __init__(self, screen_size):
        self.screen_size = screen_size
        self.screen = pygame.display.set_mode(screen_size, pygame.HWSURFACE | pygame.DOUBLEBUF)
        self.display = pygame.Surface(screen_size)  # 中间层，用于缩放效果
        pygame.display.set_caption("Engine Run!!!")
        self.world = None
        self.ui = None
        self.editor = None
        self._fader = None            # 遮挡淡出状态机（首次用到时才建）
        self._fader_owners = {}       # 叶/单位 id -> 宿主结构
        # 保留原有 shader（如果有）
        # self.screen_shader = pygame_shaders.DefaultScreenShader(self.display)  # 暂不启用

    def set_world(self, world):
        self.world = world

    def set_ui_manager(self, ui_manager):
        self.ui = ui_manager

    def set_editor(self, editor):
        self.editor = editor

    def check(self):
        if self.world and self.editor and self.ui:
            return True
        else:
            raise Exception("Resources are not intact")

    def render(self, game_mode, dt=None):
        if dt is None:
            dt = 1.0 / 60.0
        self.display.fill((30, 30, 30))
        # 绘制世界
        self._draw_world(game_mode, dt)
        # 绘制 UI
        self.world.draw_active_blocks(self.display)
        self._draw_ui(game_mode)
        # 将 display 缩放到屏幕（可能后期支持 shader）
        self.screen.blit(pygame.transform.scale(self.display, self.screen_size), (0, 0))
        # 可选：应用 shader
        pygame.display.flip()


    def _draw_world(self, game_mode, dt=1.0 / 60.0):
        # 绘制网格（编辑模式）
        if game_mode == GameMode.EDIT:
            self._draw_grid()

        # 绘制墙体预览
        if self.editor.wall_edit_type and self.editor.temp_point:
            self._draw_wall_preview()

        # ---- 绘制所有图层（单一真值源：LayerStack 顺序 / 可见性）----
        # 地形(Tiled 瓦片)垫底，遇到即画；动态对象跨层用“遮挡感知”深度排序
        # （与 OpenGL 后端一致：普通/非遮挡 Structure 为原子，House 叶子逐片展开，
        #  使内部角色能正确插到后墙与前墙之间）。
        dynamic = []
        for bucket in self.world.layers.iter_visible():
            spec = bucket.spec
            if spec.kind == LayerKind.EFFECT:
                self._draw_particle_bucket(bucket)
                continue
            for obj in bucket.sprites.sprites():
                if not getattr(obj, "in_active_block", True):
                    continue
                if isinstance(obj, Tiled):
                    for leaf in leaf_sprites_of(obj):
                        self._draw_sprite(leaf, game_mode)
                else:
                    dynamic.append(obj)

        # 遮挡淡入淡出：谁挡住焦点（通常是玩家）谁淡到半透明
        units, owners, _focus = self._occlusion_pass(dynamic, dt)
        self._fader_owners = owners or {}
        if units is None:
            units = depth_sorted_draw_units(dynamic)
        for unit in units:
            a = self._alpha_of(unit)
            for leaf in leaf_sprites_of(unit):
                self._draw_sprite(leaf, game_mode, alpha=a)

        # 编辑模式：放置预览绘制一次
        if game_mode == GameMode.EDIT and self.editor:
            self.editor.draw_preview(self.display)

        # 绘制中心点
        pygame.draw.circle(self.display, (255,0,0), self.display.get_rect().center, 1)

        # 绘制传送门连线
        # 连线列表由 World 在"锁定目标变化"时重建并持有；这里只读不清理
        # （旧代码每帧 clear()，会让只在锁定切换时重建的列表一帧后就被抹掉）。
        if hasattr(self.world, 'pending_portal_lines'):
            for start, end in self.world.pending_portal_lines:
                start_screen = self.world.camera.world_to_screen(start)
                end_screen = self.world.camera.world_to_screen(end)
                pygame.draw.line(self.display, (255, 255, 255), start_screen, end_screen, 2)

    # ---------- 遮挡淡入淡出 ----------
    def _occlusion_pass(self, dyn, dt):
        """算出"这一帧谁该淡"。返回 (units, owners, focus)；units=None 表示不启用。"""
        if not bool(getattr(settings, "OCCLUSION_FADE", False)):
            return None, None, None
        world = self.world
        focus = getattr(world, "active_player", None) if world is not None else None
        if focus is None:
            if self._fader is not None:
                self._fader.update((), dt)
            return None, None, None
        if self._fader is None:
            from src.utils.layer_v2 import OcclusionFader
            self._fader = OcclusionFader(
                target=float(getattr(settings, "OCCLUSION_FADE_ALPHA", 0.35)),
                speed=float(getattr(settings, "OCCLUSION_FADE_SPEED", 5.0)),
            )
        from src.utils.layer_v2 import occlusion_frame
        units, owners, occ = occlusion_frame(dyn, focus)
        self._fader.update(occ, dt)
        return units, owners, focus

    def _alpha_of(self, unit):
        if self._fader is None:
            return 1.0
        return self._fader.alpha_of(unit, getattr(self, "_fader_owners", {}).get(id(unit)))

    def _draw_sprite(self, sprite, game_mode, alpha: float = 1.0):
        """绘制单个叶子精灵（普通对象 或 Structure 展开的叶子）。

        alpha<1 时用一份缓存的半透明副本 —— 直接改 sprite.image 会污染引擎状态。
        """
        if alpha <= 0.004:
            return
        if not getattr(sprite, "in_active_block", True):
            return
        if not hasattr(sprite, "image") or not hasattr(sprite, "world_position"):
            return
        world_center = sprite.world_position.center
        screen_center = self.world.camera.world_to_screen(world_center)
        zoom = self.world.camera.zoom
        # 缩放图像
        if not hasattr(sprite, '_scaled_cache') or sprite._scaled_zoom != zoom:
            scaled_w = max(1, int(sprite.world_position.width * zoom))
            scaled_h = max(1, int(sprite.world_position.height * zoom))
            sprite._scaled_image = pygame.transform.scale(sprite.image, (scaled_w, scaled_h))
            sprite._scaled_zoom = zoom
            sprite._scaled_fade_key = None
        scaled_image = sprite._scaled_image
        if alpha < 0.999:
            fa = int(round(max(0.0, min(1.0, alpha)) * 255))
            key = (id(sprite._scaled_image), fa)
            if getattr(sprite, "_scaled_fade_key", None) != key:
                faded = sprite._scaled_image.copy()
                faded.set_alpha(fa, pygame.RLEACCEL)
                sprite._scaled_fade_image = faded
                sprite._scaled_fade_key = key
            scaled_image = sprite._scaled_fade_image
        draw_pos = (screen_center[0] - scaled_image.get_width()//2,
                    screen_center[1] - scaled_image.get_height()//2)
        self.display.blit(scaled_image, draw_pos)

        # 编辑模式显示边界框
        if game_mode == GameMode.EDIT:
            rect = pygame.Rect(draw_pos[0], draw_pos[1],
                               scaled_image.get_width(), scaled_image.get_height())
            pygame.draw.rect(self.display, (255, 0, 0), rect, 1)

    def _draw_particle_bucket(self, bucket):
        """按旧特效逻辑绘制粒子（具备 pos/image/size 的对象）。"""
        for particle in bucket.sprites.sprites():
            if not (hasattr(particle, "pos") and hasattr(particle, "image")):
                continue
            screen_pos = self.world.camera.world_to_screen(particle.pos)
            size = getattr(particle, "size", 1)
            scaled_size = int(size * self.world.camera.zoom)
            scaled_image = pygame.transform.scale(particle.image, (scaled_size*2, scaled_size*2))
            self.display.blit(scaled_image,
                              (screen_pos[0]-scaled_size, screen_pos[1]-scaled_size))


    def _draw_grid(self):
        zoom = self.world.camera.zoom
        offset_x, offset_y = self.world.camera.offset
        screen_w, screen_h = self.display.get_size()
        left = offset_x
        top = offset_y
        right = offset_x + screen_w / zoom
        bottom = offset_y + screen_h / zoom
        start_col = math.floor(left / self.world.grid_size)
        end_col = math.ceil(right / self.world.grid_size)
        start_row = math.floor(top / self.world.grid_size)
        end_row = math.ceil(bottom / self.world.grid_size)
        for col in range(start_col, end_col+1):
            world_x = col * self.world.grid_size
            screen_x = (world_x - offset_x) * zoom
            if 0 <= screen_x <= screen_w:
                pygame.draw.line(self.display, (60,60,60), (screen_x, 0), (screen_x, screen_h))
        for row in range(start_row, end_row+1):
            world_y = row * self.world.grid_size
            screen_y = (world_y - offset_y) * zoom
            if 0 <= screen_y <= screen_h:
                pygame.draw.line(self.display, (60,60,60), (0, screen_y), (screen_w, screen_y))

    def _draw_wall_preview(self):
        # 与原来相同
        points = self.editor.wall_points
        temp = self.editor.temp_point
        screen_points = [self.world.camera.world_to_screen(p) for p in points]
        temp_screen = self.world.camera.world_to_screen(temp)
        if self.editor.wall_edit_type == 'polygon':
            for pt in screen_points:
                pygame.draw.circle(self.display, (0,255,0), (int(pt[0]), int(pt[1])), 5)
            if len(screen_points) > 1:
                pygame.draw.lines(self.display, (0,255,0), False, screen_points, 2)
            if screen_points:
                pygame.draw.line(self.display, (0,255,0), screen_points[-1], temp_screen, 2)
        elif self.editor.wall_edit_type == 'circle':
            if screen_points:
                center = screen_points[0]
                radius = math.hypot(temp_screen[0]-center[0], temp_screen[1]-center[1])
                pygame.draw.circle(self.display, (0,255,0), (int(center[0]), int(center[1])), int(radius), 2)
        elif self.editor.wall_edit_type == 'rectangle':
            if screen_points:
                start = screen_points[0]
                rect = pygame.Rect(min(start[0], temp_screen[0]), min(start[1], temp_screen[1]),
                                   abs(temp_screen[0]-start[0]), abs(temp_screen[1]-start[1]))
                pygame.draw.rect(self.display, (0,255,0), rect, 2)

    def _draw_ui(self, game_mode):
        # 调用 UI 管理器绘制
        self.ui.draw(self.display, game_mode)