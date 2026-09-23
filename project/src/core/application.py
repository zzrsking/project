import pygame
import threading

# from OpenGL.raw.GLES1.NV import fence
from pygame import QUIT, KEYDOWN, MOUSEBUTTONDOWN, K_TAB, K_LEFT, K_RIGHT, K_UP, K_DOWN
from src.core.state import GameMode
from src.core.command import Command
from src.game.world import GameWorld
from src.ui.ui_manager import UIManager
from src.editor.editor_manager import EditorManager
import settings
import setup
from src.utils.helpers import clamp
import logging

from utils.helpers import print_info

logging.basicConfig(level=logging.INFO)

tmx_data = setup.load_tmx_data("terrain\\map.tmx")
layers = setup.get_tmx_layers(tmx_data)
all_layer_data = setup.get_tmx_all_layers_data(tmx_data)


class Application:
    def __init__(self, screen_size=(1200, 800)):
        self.screen_size = screen_size
        self.clock = pygame.time.Clock()
        self.running = True

        # 创建子系统
        if settings.RENDER_BACKEND == 'opengl':
            from src.renderer.backend.opengl_renderer import OpenGLRenderer
            self.renderer = OpenGLRenderer(screen_size)
        else:
            from src.renderer.backend.pygame_renderer import PygameRenderer
            self.renderer = PygameRenderer(screen_size)
        self.world = GameWorld(screen_size)
        self.world.set_dialogue_callbacks(
            start_cb=self._on_dialogue_start,
            update_cb=self._on_dialogue_update,
            end_cb=self._on_dialogue_end,
            event_cb = self._on_game_event
        )

        self.install_test_environment()

        self.editor = EditorManager(self.world)
        self.ui = UIManager(pygame.rect.Rect(0, 0, *screen_size), self.world, self.editor)
        self.renderer.set_world(self.world)
        self.renderer.set_ui_manager(self.ui)
        self.renderer.set_editor(self.editor)
        Command.register(self.editor, name="editor")
        Command.register(self.world, name="world")
        Command.register(self.renderer, name="renderer")
        # 模式管理
        self.game_mode = GameMode.EDIT
        self.prev_mode = None

        # 将编辑器与 UI 关联（用于属性面板）
        self.editor.set_ui(self.ui)


    def install_test_environment(self):
        # 在引擎层(特殊层+内置对象层)之上叠加地图层(TMX)，不重建/不清空世界
        for spec in layers:
            self.world.layers.add_layer(spec)
        setup.load_tmx_to_world(tmx_data, self.world)
        # ---- 测试层：竖墙 贴 横墙的边（不是贴在横墙下面） ----
        # 竖墙语义 = (长, 高)：网格 1×(长+高)，宽恒1，纵向列。
        # 连接规则只在竖墙：顶部 未连接33 / 向右连接8(墙在左) / 向左连接10(墙在右)；
        # 横墙贴边时不换纹理(保持原有 40/41/42)。
        # ---- 测试层：外凸“几字形”—— 顶部一条横墙，两侧各一条竖墙向下垂，
        #      竖墙只在上端(头)与横墙端角接上(转角=8/10)，下端(尾)自由开口(45)。
        from src.core.build_game.horizontalwall import HorizontalWoodWall
        from src.core.build_game.verticalwall import (
            VerticalWoodWall, register_wall_family_corner_rules)
        dock_log = []
        register_wall_family_corner_rules(log=dock_log)
        h_wall = HorizontalWoodWall((5, 3), (900, 300))     # 顶部横墙 5列x3行
        # 右侧竖墙：向下垂，头(上端)贴横墙右端角(转角)。
        # 连接单元 = 端口：ports[name] 只代表位置，方向由两侧 side 显式指定。
        v_right = VerticalWoodWall(6, 3, center=(1600, 300))
        v_right.ports["top"].connect_to(
            h_wall.port("top_right"), self_side="left", peer_side="right")
        # 左侧竖墙（对称）：v_left 头朝右、横墙 top_left 在左缘 → 方向用 right / left。
        v_left = VerticalWoodWall(6, 3, center=(-300, 300))
        v_left.ports["top"].connect_to(
            h_wall.port("top_left"), self_side="right", peer_side="left")
        hbr_wall = HorizontalWoodWall((5, 3), (900, 600))
        hbl_wall = HorizontalWoodWall((5, 3), (900, 900))
        # ---- 竖墙尾巴(tail)段 连 下部横墙：一个向内拐、一个向外拐 ----
        # 方向不掺进端口名（ports["tail"] 只是位置），两侧 side 由调用方显式给出；
        # 跨不跨半格由竖墙自己判定（on_topology_settled 读 tail 连接方向算出 _tail_cross），d
        # 调用方不写死 cross。
        # 左竖墙 v_left：hbl 的 top_right.right 边 ↔ v_left 尾巴的 left 边 → vl 判为向外拐(cross=True)。
        hbl_wall.port("top_right").connect_to(
            v_left.ports["tail"], self_side="right", peer_side="left")
        # hbl_wall.port("top_right").disconnect()
        Command.register(hbl_wall.port("top_right"), "p1")
        Command.register(v_left.ports["tail"], "p2")
        # 右竖墙 v_right：hbr 的 top_right.right 边 ↔ v_right 尾巴的 left 边 → vr 判为向内拐(cross=False)。
        hbr_wall.port("top_left").connect_to(
            v_right.ports["tail"], self_side="left", peer_side="right")

        # ---- 测试层：矩形房子 RectangleHouse（地板+四墙端口组装） ----
        # 尺寸按“长×宽×高”映射：地板 length×width、横墙跨 length、竖墙向上 height。
        # 放在既有墙的下方，避免重叠遮挡。
        # House 是“自遮挡”组合结构：把地板+四面墙合并成一套叶子，渲染时叶子逐片
        # 参与深度排序 —— 角色/物体在房子内部时会被正确插到后墙与前墙之间。
        from src.core.build_game.house import House
        house = House.build(10, 10, 3, center=(900, 1250), use_wood=True)
        for hl in house.build_log:
            continue
            print_info(f"[build_game] 矩形房连接观测: {hl}")

        for msg in dock_log:
            continue
            print_info(f"[build_game] 端口观测: {msg}")

        # ---- 测试层：桌子（Table，双层格子网格） ----
        # table 层 = 桌面本体（House Decoration 第 30 号木桌板面，满铺）；
        # object 层 = 摆在桌面上的东西（此处用第 3 号盆栽，验证双层互不挤占）。
        # 放在房子左侧空地上，避免与既有墙/房子重叠。
        from src.core.build_game.on_the_table import WoodTable, register_table_rules
        register_table_rules()                      # 幂等：桌↔桌拼接规则
        table = WoodTable((1, 1), (300, 1250))      # 1×1 = 一张单桌（正好是 30 号那张）
        self.world.add_game_object(table, "test")
        try:
            from src.game.entity import TablePlantTile
            plant = TablePlantTile()
            if table.place_object((0, 0), plant) is not None:
                self.world.add_game_object(plant, "test")
        except Exception as e:
            print_info(f"[build_game] 桌上物件放置跳过: {e}")

        from src.core.build_game.hall import Hall, EW, NS, connect_halls
        hall1 = Hall.build(3, 10, 3, center=(900, -200), use_wood=True,orientation=NS)
        hall2 = Hall.build(3, 10, 3, center=(1200, -100), use_wood=True,orientation=NS)
        connect_halls(hall2, hall1, a_end="north", b_end="south")

        # ---- 测试层：房子 × 走廊（两种朝向，一条边接若干走廊） ----
        # house_edge：把房子的一条边拆成一排【单格口】——
        #   · 走廊开口认领它正对的那几格：NS 走廊接南边、EW 走廊接东边；
        #   · 没被认领的格自动补上【小墙段】（原来那面墙按剩余格宽拆开），
        #     那条边看起来仍是一道连续的墙；
        #   · 接上之后走廊那端的墙头会自动切成"跑通"纹（不再画自由端收口），
        #     与房子侧墙看起来是一整堵。
        # 观测记录留在 self.house_edge_reports 里（tests/test_application_house_hall.py 会查）。
        from src.core.build_game.house_edge import connect_halls_to_house
        hall_s1 = Hall.build(2, 4, 3, center=(0, 0), orientation=NS)   # 南边：南北向走廊 A
        hall_s2 = Hall.build(2, 4, 3, center=(0, 0), orientation=NS)   # 南边：南北向走廊 B
        hall_e1 = Hall.build(4, 5, 3, center=(0, 0), orientation=EW)   # 东边：东西向走廊
        hall_e2 = Hall.build(4, 2, 3, center=(0, 0), orientation=EW)   # 东边：东西向走廊
        self.house_edge_reports = {
            # 南边（宽 10 格）：两条走廊各占 0..1 / 7..8，中间 3..5 格补一段小墙
            "south": connect_halls_to_house(house, "south", [(hall_s1, 2), (hall_s2, 7)]),
            # 东边（高 10 格）：EW 走廊占 4..5 行，上下各补一段竖墙小段
            "east": connect_halls_to_house(house, "east", [(hall_e1, 1)]),
        }
        self.demo_halls = [hall1, hall2, hall_s1, hall_s2, hall_e1, hall_e2]

        # 竖墙/横墙进 world（test 层，见 settings）：经 add_game_object 注册进空间网格/block，
        # 随视口激活而显隐；Structure.is_collidable=False，故不会误进物理引擎。

        house2 = House.build(10, 10, 3, center=(2000, 1250), use_wood=True)
        from src.core.build_game.opening import open_house_side, make_edge_ports
        open_house_side(house2, "east", disconnect=True)
        for wall in (h_wall, v_right, v_left, hbl_wall, hbr_wall, house, house2, hall1, hall2,
                     hall_s1, hall_s2, hall_e1, hall_e2):
            self.world.add_game_object(wall, "test")

    def run(self):
        while self.running:
            dt = self.clock.tick(60) / 1000.0
            self._handle_events(dt)
            self._update(dt)
            self._render(dt)

    def _handle_events(self, dt):
        for event in pygame.event.get():
            if event.type == QUIT:
                self.running = False
                import os
                """
                os._exit(0) 直接调用 C 语言的 _exit() 系统调用，
                告诉操作系统“立即终止这个进程”。操作系统会回收所有内存、关闭所有句柄，所有子线程（无论是否 daemon）瞬间灰飞烟灭，
                没有任何回旋余地。不需要遍历，不需要 ctypes，一行搞定。--deepseek
                """
                os._exit(0)
            # 让 UI 处理事件，并检查模式切换
            handled, target_mode = self.ui.handle_event(event)
            if target_mode is not None:
                if target_mode == GameMode.PLAY:
                    self.prev_mode = self.game_mode
                    self.game_mode = GameMode.PLAY
                elif target_mode == GameMode.EDIT:
                    self.prev_mode = self.game_mode
                    self.game_mode = GameMode.EDIT
                elif target_mode == GameMode.SETTINGS:
                    self.prev_mode = self.game_mode
                    self.game_mode = GameMode.SETTINGS
            elif target_mode is None and handled:  # 返回
                self.game_mode = self.prev_mode if self.prev_mode else GameMode.EDIT
            # 如果事件没有被 UI 消费，且当前是编辑器模式，则交给编辑器
            if not handled and self.game_mode == GameMode.EDIT:
                self.editor.handle_event(event, dt)
            # 如果当前是游戏模式，交给世界
            if not handled and self.game_mode == GameMode.PLAY:
                self.world.handle_event(event, dt)

        # 键盘事件（部分全局快捷键）
        keys = pygame.key.get_pressed()
        if self.game_mode == GameMode.EDIT:
            speed = 10 / self.world.camera.zoom  # 世界坐标移动速度
            dx = dy = 0
            if keys[K_LEFT]:
                dx -= speed
            if keys[K_RIGHT]:
                dx += speed
            if keys[K_UP]:
                dy -= speed
            if keys[K_DOWN]:
                dy += speed
            if dx != 0 or dy != 0:
                self.world.camera.move(dx, dy)

    def _update(self, dt):
        # 更新世界（物理、AI、动画等）
        self.world.update(dt, self.game_mode)
        # 更新编辑器（悬停检测等）
        if self.game_mode == GameMode.EDIT:
            # 传真实 dt：属性面板的淡入淡出此前写死 0.016，和帧率脱钩
            self.editor.update(dt)
        # 更新 UI 状态
        self.ui.update(dt, self.game_mode, self.world.active_player)

        # 摄像机更新（所有模式）
        self.world.camera.update(dt, self.game_mode.camera_lock)
        self.world.backend_camera.update(dt, self.game_mode.camera_lock)

        # 更新区块激活状态
        self.world.update_blocks(self.world.camera.get_viewport())

    def _render(self, dt):
        self.renderer.render(self.game_mode, dt)

    def _on_dialogue_start(self, node, npc):
        self.prev_mode = self.game_mode
        self.game_mode = GameMode.DIALOGUE
        self.ui.current_mode = GameMode.DIALOGUE  # 让 UI 知道当前模式
        self.ui.dialogue_ui.update_ui(node)
        self.ui.dialogue_ui.option_callback = self.world.select_dialogue_option

    def _on_dialogue_update(self, node):
        self.ui.dialogue_ui.update_ui(node)

    def _on_dialogue_end(self):
        self.game_mode = self.prev_mode if self.prev_mode else GameMode.EDIT
        self.ui.current_mode = self.game_mode
        self.ui.dialogue_ui.option_callback = None

    # 添加事件处理方法
    def _on_game_event(self, event_id):
        """处理对话触发的游戏事件"""
        print_info(f"游戏事件: {event_id}")
        # 根据 event_id 执行具体逻辑，例如：
        # if event_id == "open_gate": self.world.open_gate()

