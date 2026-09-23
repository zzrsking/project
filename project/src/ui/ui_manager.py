import pygame
import asyncio
from pygame import MOUSEBUTTONDOWN

from game import camera
from game.camera import Camera
from serializer import label
from src.ui.editor_ui import EditorInterface
from src.ui.gameplay_ui import GameplayInterface
from src.ui.settings_ui import SettingsInterface
from src.ui.dialogue_ui import DialogueInterface
from src.core.state import GameMode
from src.utils.helpers import threaded
import serializer

class UIManager:
    def __init__(self, screen_rect, world, editor):
        self.screen_rect = screen_rect
        self.world = world
        self.editor_ui = EditorInterface(screen_rect, world, editor)
        self.gameplay_ui = GameplayInterface(screen_rect)
        self.settings_ui = SettingsInterface(screen_rect)
        self.dialogue_ui = DialogueInterface(screen_rect)

        # ---- 双向连接世界和对话UI ----
        self.world.ui_ref = self.dialogue_ui
        self.dialogue_ui.set_callbacks(
            option_cb=self.world.select_dialogue_option,
            confirm_cb=self.world.confirm_dialogue_option,
            cancel_cb=self.world.cancel_dialogue_option
        )

        self.current_mode = None
        self.settings_ui.save_func = self.save_func
        self.settings_ui.load_func = self.load_func

    def update(self, dt, game_mode, player):
        self.current_mode = game_mode
        mouse_pos = pygame.mouse.get_pos()
        if self.current_mode == GameMode.EDIT:
            self.editor_ui.update(mouse_pos)
        if self.current_mode == GameMode.PLAY:
            self.gameplay_ui.update(mouse_pos)
        if self.current_mode == GameMode.DIALOGUE:
            self.dialogue_ui.update_player(player)
        if self.current_mode == GameMode.SETTINGS:
            self.settings_ui.update(mouse_pos, dt)
        # 其他UI（如对话UI）无需主动更新

    def handle_event(self, event):
        # 处理模式切换按钮
        if event.type == MOUSEBUTTONDOWN and event.button == 1:
            mouse_pos = pygame.mouse.get_pos()
            if self.current_mode == GameMode.EDIT:
                buttons = self.editor_ui.mode_buttons
            elif self.current_mode == GameMode.PLAY:
                buttons = self.gameplay_ui.mode_buttons
            elif self.current_mode == GameMode.SETTINGS:
                buttons = self.settings_ui.mode_buttons.values()
            else:
                buttons = []
            for btn in buttons:
                if btn.rect.collidepoint(mouse_pos):
                    if btn.text == "Play":
                        return (True, GameMode.PLAY)
                    elif btn.text == "Editor":
                        return (True, GameMode.EDIT)
                    elif btn.text == "Settings":
                        return (True, GameMode.SETTINGS)
                    elif btn == self.settings_ui.mode_buttons["返回"]:
                        return (True, None)

        # 按当前模式分发事件
        if self.current_mode == GameMode.EDIT:
            self.editor_ui.handle_event(event)
        elif self.current_mode == GameMode.PLAY:
            self.gameplay_ui.handle_event(event)
        elif self.current_mode == GameMode.SETTINGS:
            self.settings_ui.handle_event(event)
        elif self.current_mode == GameMode.DIALOGUE:
            self.dialogue_ui.handle_event(event)

        return (False, None)

    def draw(self, display, game_mode):
        if game_mode == GameMode.EDIT:
            self.editor_ui.draw(display)
        elif game_mode == GameMode.PLAY:
            mouse_pos = pygame.mouse.get_pos()
            self.gameplay_ui.draw(display, self.world.camera, mouse_pos, self.world.active_player)
            # self._draw_gameplay_hud(display)
        elif game_mode == GameMode.SETTINGS:
            mouse_pos = pygame.mouse.get_pos()
            self.settings_ui.draw(display, mouse_pos)
        elif game_mode == GameMode.DIALOGUE:
            if self.world and self.world.active_player:
                mgr = getattr(self.world.active_player, 'dialogue_manager', None)
                if mgr and mgr.active:
                    node = mgr.current_node
                    # 对话UI绘制（包含弹窗）
                    self.dialogue_ui.draw(display, node)
                else:
                    # 如果对话管理器不存在或不活跃，但模式为DIALOGUE，可以绘制一个提示
                    pass

    def save_func(self):
        """存档：走场景格式（地形由地图文件重建，不逐个保存）

        旧实现是 `serializer.save()` 逐对象存 —— test 地图光地形就有 11400 个地块，
        文件 12.7MB、序列化 6.7s，其中 99% 是本来就能重建的地形。
        """
        from src.core.scene_io import save_scene, DEFAULT_SCENE_FILE
        try:
            save_scene(self.world, DEFAULT_SCENE_FILE)
        except Exception as e:
            print(f"[save] 存档失败: {e}")

    def backup_load_func(self):
        """同步读档（与 load_func 同一套逻辑，只是不开线程）"""
        from src.core.scene_io import load_scene, DEFAULT_SCENE_FILE
        try:
            load_scene(self.world, DEFAULT_SCENE_FILE)
        except Exception as e:
            print(f"[load] 读档失败: {e}")

    @threaded
    def load_func(self):
        """读档：重建地形 → 放回对象 → 重连结构

        注意旧实现的坑：它在循环里逐个 add_game_object，任何一次抛异常都会
        **中断整次读档**（表现就是"只有前半截对象回来了"）。现在逐对象加了保护，
        并且地形不再参与这个循环。
        """
        from src.core.scene_io import load_scene, DEFAULT_SCENE_FILE
        try:
            load_scene(self.world, DEFAULT_SCENE_FILE)
        except Exception as e:
            print(f"[load] 读档失败: {e}")
