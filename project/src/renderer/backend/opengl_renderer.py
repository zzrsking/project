import pygame
import pymunk
from OpenGL.GL import *
from OpenGL.GL import shaders
import numpy as np
import ctypes
from src.core.state import GameMode
from src.utils.helpers import get_body_collision_vertices
from src.game.entity import Tiled
from src.core.build_game.structure import Structure
from src.utils.layer_v2 import depth_sorted_draw_units, occlusion_frame, OcclusionFader
import math
import settings



class OpenGLRenderer:
    def __init__(self, screen_size):
        self.screen_size = screen_size
        self.world = None
        self.ui_manager = None
        self.editor_manager = None

        self._setup_opengl()

        self.texture_cache = {}
        self.world_shader = self._compile_world_shader()
        self.screen_shader = self._compile_screen_shader()
        self.grid_shader = self._compile_grid_shader()

        self.ui_surface = pygame.Surface(screen_size, pygame.SRCALPHA)
        self.ui_texture = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, self.ui_texture)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, *screen_size, 0, GL_RGBA, GL_UNSIGNED_BYTE, None)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)

        self._setup_screen_quad()

        # ---- 世界对象绘制缓冲（VAO/VBO） ----
        self.world_vao = glGenVertexArrays(1)
        self.world_vbo = glGenBuffers(1)
        self.world_vbo_capacity = 60000  # 初始分配 6 万个顶点（约 1 万个四边形）

        glBindVertexArray(self.world_vao)
        glBindBuffer(GL_ARRAY_BUFFER, self.world_vbo)
        glBindVertexArray(self.world_vao)
        glBindBuffer(GL_ARRAY_BUFFER, self.world_vbo)
        glBufferData(GL_ARRAY_BUFFER, self.world_vbo_capacity * 4 * 4, None, GL_DYNAMIC_DRAW)
        glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 4 * 4, ctypes.c_void_p(0))
        glEnableVertexAttribArray(0)
        glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, 4 * 4, ctypes.c_void_p(2 * 4))
        glEnableVertexAttribArray(1)
        glBindVertexArray(0)

        # ---- 静态地形缓冲（只在地形集合变化时重建，避免每帧扫描上万块） ----
        self.static_vao = glGenVertexArrays(1)
        self.static_vbo = glGenBuffers(1)
        self._static_vbo_cap = 0          # 顶点容量
        self._static_quad_count = 0
        self._static_cache_rev = None     # 对应 (geom_revision, 可见层签名)
        self._static_dynamic = []         # 非地形(动态)对象列表，重建时一并缓存
        self._static_segments = []        # [(tex_id, first_quad, quad_count)] 全静态段
        self._fader = None                # 遮挡淡出状态机（首次用到时才建）
        glBindVertexArray(self.static_vao)
        glBindBuffer(GL_ARRAY_BUFFER, self.static_vbo)
        glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 4 * 4, ctypes.c_void_p(0))
        glEnableVertexAttribArray(0)
        glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, 4 * 4, ctypes.c_void_p(2 * 4))
        glEnableVertexAttribArray(1)
        glBindVertexArray(0)

        # ---- 网格绘制缓冲（VAO/VBO） ----
        self.grid_vao = glGenVertexArrays(1)
        self.grid_vbo = glGenBuffers(1)
        self.grid_max_vertices = 40000
        glBindVertexArray(self.grid_vao)
        glBindBuffer(GL_ARRAY_BUFFER, self.grid_vbo)
        glBufferData(GL_ARRAY_BUFFER, self.grid_max_vertices * 2 * 4, None, GL_DYNAMIC_DRAW)
        glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 2 * 4, ctypes.c_void_p(0))
        glEnableVertexAttribArray(0)
        glBindVertexArray(0)

        self.texture_cache_legacy = {}


    # ---------- 注入引用 ----------
    def set_world(self, world):
        self.world = world

    def set_ui_manager(self, ui_manager):
        self.ui_manager = ui_manager

    def set_editor(self, editor):
        self.editor_manager = editor

    # ---------- OpenGL 初始化 ----------
    def _setup_opengl(self):
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glClearColor(0.117, 0.117, 0.117, 1.0)
        glViewport(0, 0, *self.screen_size)

    # ---------- 着色器编译 ----------
    def _compile_world_shader(self):
        VERT = """
        #version 330 core
        layout(location = 0) in vec2 a_pos;   // 世界像素坐标
        layout(location = 1) in vec2 a_uv;
        uniform vec2 u_screen_size;
        uniform vec2 u_camera_offset;         // 相机世界偏移
        uniform float u_zoom;                 // 相机缩放
        out vec2 v_uv;
        void main() {
            vec2 sp = (a_pos - u_camera_offset) * u_zoom;   // 世界 -> 屏幕像素
            vec2 ndc = sp / u_screen_size * 2.0 - 1.0;
            ndc.y = -ndc.y;
            gl_Position = vec4(ndc, 0.0, 1.0);
            v_uv = a_uv;
        }
        """
        FRAG = """
        #version 330 core
        in vec2 v_uv;
        uniform sampler2D u_texture;
        uniform float u_alpha;        // 0..1：遮挡淡出用；不淡时为 1.0
        out vec4 fragColor;
        void main() {
            vec4 c = texture(u_texture, v_uv);
            fragColor = vec4(c.rgb, c.a * u_alpha);
        }
        """
        try:
            vert = shaders.compileShader(VERT, GL_VERTEX_SHADER)
            frag = shaders.compileShader(FRAG, GL_FRAGMENT_SHADER)
            prog = shaders.compileProgram(vert, frag)
            return prog
        except RuntimeError as e:
            print(f"世界着色器编译错误: {e}")
            raise

    def _compile_screen_shader(self):
        VERT = """
        #version 330 core
        layout(location = 0) in vec2 a_pos;
        layout(location = 1) in vec2 a_uv;
        out vec2 v_uv;
        void main() {
            gl_Position = vec4(a_pos, 0.0, 1.0);
            v_uv = a_uv;
        }
        """
        FRAG = """
        #version 330 core
        in vec2 v_uv;
        uniform sampler2D u_texture;
        out vec4 fragColor;
        void main() {
            fragColor = texture(u_texture, v_uv);
        }
        """
        try:
            vert = shaders.compileShader(VERT, GL_VERTEX_SHADER)
            frag = shaders.compileShader(FRAG, GL_FRAGMENT_SHADER)
            prog = shaders.compileProgram(vert, frag)
            return prog
        except RuntimeError as e:
            print(f"全屏着色器编译错误: {e}")
            raise

    def _compile_grid_shader(self):
        VERT = """
        #version 330 core
        layout(location = 0) in vec2 a_pos;
        uniform vec2 u_screen_size;
        void main() {
            vec2 ndc = a_pos / u_screen_size * 2.0 - 1.0;
            ndc.y = -ndc.y;
            gl_Position = vec4(ndc, 0.0, 1.0);
        }
        """
        FRAG = """
        #version 330 core
        uniform vec3 u_color;
        out vec4 fragColor;
        void main() {
            fragColor = vec4(u_color, 1.0);
        }
        """
        try:
            vert = shaders.compileShader(VERT, GL_VERTEX_SHADER)
            frag = shaders.compileShader(FRAG, GL_FRAGMENT_SHADER)
            prog = shaders.compileProgram(vert, frag)
            return prog
        except RuntimeError as e:
            print(f"网格着色器编译错误: {e}")
            raise

    # ---------- 全屏四边形 ----------
    def _setup_screen_quad(self):
        vertices = np.array([
            -1.0, -1.0, 0.0, 0.0,
             1.0, -1.0, 1.0, 0.0,
            -1.0,  1.0, 0.0, 1.0,
            -1.0,  1.0, 0.0, 1.0,
             1.0, -1.0, 1.0, 0.0,
             1.0,  1.0, 1.0, 1.0,
        ], dtype=np.float32)
        self.screen_vao = glGenVertexArrays(1)
        self.screen_vbo = glGenBuffers(1)
        glBindVertexArray(self.screen_vao)
        glBindBuffer(GL_ARRAY_BUFFER, self.screen_vbo)
        glBufferData(GL_ARRAY_BUFFER, vertices.nbytes, vertices, GL_STATIC_DRAW)
        stride = 4 * 4
        glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, stride, ctypes.c_void_p(0))
        glEnableVertexAttribArray(0)
        glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, stride, ctypes.c_void_p(2 * 4))
        glEnableVertexAttribArray(1)
        glBindVertexArray(0)

    # ---------- 纹理管理 ----------
    def _get_texture(self, surface):
        key = id(surface)
        if key in self.texture_cache:
            return self.texture_cache[key]
        if surface.get_bytesize() != 4:
            surface = surface.convert_alpha()
        data = pygame.image.tostring(surface, 'RGBA', True)
        width, height = surface.get_size()
        tex_id = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, tex_id)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, width, height, 0, GL_RGBA, GL_UNSIGNED_BYTE, data)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        self.texture_cache[key] = tex_id
        return tex_id

    # ---------- 遮挡淡入淡出 ----------
    def _occlusion_pass(self, dyn, dt):
        """算出"这一帧谁该淡"，返回 (units, owners, focus)。

        返回 units=None 表示这一帧不启用淡出（调用方走原路径，零开销）。
        判定是通用规则：凡是排在焦点之后且与焦点【像素实际相交】的单位都算遮挡者，
        不针对任何具体结构类型写特例。
        """
        if not bool(getattr(settings, "OCCLUSION_FADE", False)):
            return None, None, None
        world = self.world
        focus = getattr(world, "active_player", None) if world is not None else None
        if focus is None:
            # 没有焦点实体 -> 把之前的淡出状态收回去（别让墙卡在半透明）
            if self._fader is not None:
                self._fader.update((), dt)
            return None, None, None

        if self._fader is None:
            self._fader = OcclusionFader(
                target=float(getattr(settings, "OCCLUSION_FADE_ALPHA", 0.35)),
                speed=float(getattr(settings, "OCCLUSION_FADE_SPEED", 5.0)),
            )
        units, owners, occ = occlusion_frame(dyn, focus)
        self._fader.update(occ, dt)
        return units, owners, focus

    # ---------- 主渲染入口 ----------
    def render(self, game_mode, dt=None):
        """dt: 秒。缺省用 1/60 兜底，保证不传 dt 的调用方行为不变。"""
        if dt is None:
            dt = 1.0 / 60.0
        P = bool(settings.PROFILE_RENDER)
        if P:
            import time
            if not hasattr(self, "_prof"):
                self._prof = {"world": 0.0, "rest": 0.0}
                self._prof_n = 0
            t0 = time.perf_counter()

        glClear(GL_COLOR_BUFFER_BIT)

        # 1. 清空 ui_surface
        self.ui_surface.fill((0, 0, 0, 0))

        # 2. 绘制 GPU 部分（网格 + 世界对象）
        self._draw_world_gpu(game_mode, dt)
        if P:
            self._prof["world"] += time.perf_counter() - t0
            t1 = time.perf_counter()

        # 2.5 端口调试特效（叠加层，不修改任何精灵贴图；受 DPM 开关控制）
        self._draw_port_markers()

        # 2.6 深度调试框（DEPTH 开关）：框=绘制范围，青线=depth 基准线，数字=depth
        if self.world is not None and settings.DISPLAY_DEPTH:
            self.world.draw_depths(self.ui_surface, self.world.camera)

        # 3. 绘制墙壁预览（编辑模式）
        if game_mode == GameMode.EDIT and self.editor_manager and hasattr(self.editor_manager, 'wall_edit_type') and self.editor_manager.temp_point:
            self._draw_wall_preview()

        # 4. 绘制放置预览（编辑模式）
        if self.editor_manager and game_mode == GameMode.EDIT:
            self.editor_manager.draw_preview(self.ui_surface)

        # 5. 绘制活动块
        if self.world and settings.DISPLAY_ACTIVE_BLOCKS:
            self.world.draw_active_blocks(self.ui_surface)

        # 5.5 世界叠加特效：传送门连线等（World 只在锁定目标变化时重建该列表，这里只读）
        # 之前只有 pygame 后端画这条线，OpenGL 后端一直没有消费方 —— 锁定传送门时看不到连线。
        if self.world is not None:
            self.world.draw_effects(self.ui_surface, self.world.camera)

        # 6. 绘制 UI
        if self.ui_manager:
            self.ui_manager.draw(self.ui_surface, game_mode)

        # 7. 上传 ui_surface 纹理并全屏显示
        data = pygame.image.tostring(self.ui_surface, 'RGBA', True)
        glBindTexture(GL_TEXTURE_2D, self.ui_texture)
        glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, *self.screen_size, GL_RGBA, GL_UNSIGNED_BYTE, data)

        glUseProgram(self.screen_shader)
        glUniform1i(glGetUniformLocation(self.screen_shader, "u_texture"), 0)
        glActiveTexture(GL_TEXTURE0)
        glBindTexture(GL_TEXTURE_2D, self.ui_texture)
        glBindVertexArray(self.screen_vao)
        glDrawArrays(GL_TRIANGLES, 0, 6)
        glBindVertexArray(0)

        pygame.display.flip()

        if P:
            self._prof["rest"] += time.perf_counter() - t1
            self._prof_n += 1
            if self._prof_n >= 120:
                n = self._prof_n
                w = self._prof["world"] / n * 1000.0
                r = self._prof["rest"] / n * 1000.0
                print(f"[PFR] world_gpu={w:.2f}ms  rest(ui/overlay/upload)={r:.2f}ms  "
                      f"total={(self._prof['world'] + self._prof['rest']) / n * 1000.0:.2f}ms"
                      f"  (last {n} frames)")
                self._prof = {"world": 0.0, "rest": 0.0}
                self._prof_n = 0

    # ---------- GPU 绘制网格 ----------
    def _draw_grid_gpu(self):
        if self.world is None:
            return

        camera = self.world.camera
        zoom = camera.zoom
        offset = camera.offset
        screen_w, screen_h = self.screen_size
        grid_size = self.world.grid_size

        left = offset[0]
        right = offset[0] + screen_w / zoom
        top = offset[1]
        bottom = offset[1] + screen_h / zoom

        start_col = int(math.floor(left / grid_size))
        end_col = int(math.ceil(right / grid_size))
        start_row = int(math.floor(top / grid_size))
        end_row = int(math.ceil(bottom / grid_size))

        vertices = []
        for col in range(start_col, end_col + 1):
            world_x = col * grid_size
            screen_x = (world_x - offset[0]) * zoom
            screen_y_top = (top - offset[1]) * zoom
            screen_y_bottom = (bottom - offset[1]) * zoom
            vertices.extend([screen_x, screen_y_top, screen_x, screen_y_bottom])
        for row in range(start_row, end_row + 1):
            world_y = row * grid_size
            screen_y = (world_y - offset[1]) * zoom
            screen_x_left = (left - offset[0]) * zoom
            screen_x_right = (right - offset[0]) * zoom
            vertices.extend([screen_x_left, screen_y, screen_x_right, screen_y])

        if not vertices:
            return

        vert_array = np.array(vertices, dtype=np.float32)
        vertex_count = len(vertices) // 2

        glUseProgram(self.grid_shader)
        glUniform2f(glGetUniformLocation(self.grid_shader, "u_screen_size"), screen_w, screen_h)
        glUniform3f(glGetUniformLocation(self.grid_shader, "u_color"), 0.3, 0.3, 0.3)

        glBindVertexArray(self.grid_vao)
        glBindBuffer(GL_ARRAY_BUFFER, self.grid_vbo)
        if vertex_count > self.grid_max_vertices:
            self.grid_max_vertices = vertex_count + 1000
            glBufferData(GL_ARRAY_BUFFER, self.grid_max_vertices * 2 * 4, None, GL_DYNAMIC_DRAW)
        glBufferSubData(GL_ARRAY_BUFFER, 0, vert_array.nbytes, vert_array)
        glDrawArrays(GL_LINES, 0, vertex_count)
        glBindVertexArray(0)

    # ---------- GPU 绘制世界对象 ----------
    def _draw_world_gpu(self, game_mode, dt=None):
        """绘制世界。静态地形(海量 Tiled)只在地形集合变化时重建一次 VBO，
        每帧仅整批绘制；真正动态的少量对象(角色/装饰/Structure)每帧单独处理。"""
        if self.world is None:
            return

        # A/B 正确性开关：关掉则退回“逐帧全扫”的基准路径，便于对比画面是否一致
        if not bool(settings.RENDER_STATIC_CACHE):
            self._draw_world_legacy(game_mode)
            return

        camera = self.world.camera
        zoom = camera.zoom
        offset = camera.offset
        screen_w, screen_h = self.screen_size

        if game_mode == GameMode.EDIT:
            self._draw_grid_gpu()

        # 静态地形：整包只上传一次，每帧整批绘制（相机 uniform 裁剪离屏），不做逐块裁剪
        self._ensure_static_cache()
        self._draw_static(offset, zoom, screen_w, screen_h)

        # 动态对象(非地形，集合已缓存，~28 个)按 in_active_block 裁剪后绘制。
        # 普通对象/非遮挡 Structure 维持“原子”基准深度排序；遮挡 Structure(House)
        # 由 depth_sorted_draw_units 逐片展开，使内部角色能插到前墙/后墙之间。
        dyn = [o for o in self._static_dynamic if getattr(o, "in_active_block", True)]
        # 遮挡淡入淡出：谁挡住焦点（通常是玩家）谁淡到半透明
        units, owners, focus = self._occlusion_pass(dyn, dt)
        if units is None:
            units = depth_sorted_draw_units(dyn)
            self._draw_object_batch(units, offset, zoom, screen_w, screen_h)
        else:
            def _alpha(u):
                return self._fader.alpha_of(u, owners.get(id(u)))
            self._draw_object_batch(units, offset, zoom, screen_w, screen_h, alpha_of=_alpha)

        # CPU 辅助绘制
        if settings.DISPLAY_SCREEN_CENTER_POINT:
            pygame.draw.circle(self.ui_surface, (255, 0, 0), center_screen := (screen_w // 2, screen_h // 2), 1)
        if self.world is not None:
            self._draw_collision_shapes()
        if game_mode == GameMode.EDIT and self.editor_manager and hasattr(self.editor_manager,
                                                                          'wall_edit_type') and self.editor_manager.temp_point:
            self._draw_wall_preview()

    def _draw_world_legacy(self, game_mode):
        """A/B 基准路径：逐帧全扫 + 按 in_active_block 裁剪（旧实现，未用静态缓存）。"""
        camera = self.world.camera
        zoom = camera.zoom
        offset = camera.offset
        screen_w, screen_h = self.screen_size
        if game_mode == GameMode.EDIT:
            self._draw_grid_gpu()

        terrain_objects = []
        other_objects = []
        for bucket in self.world.layers:
            if not bucket.spec.visible:
                continue
            for obj in bucket.sprites.sprites():
                if not obj.in_active_block:
                    continue
                if isinstance(obj, Tiled):
                    terrain_objects.append(obj)
                else:
                    other_objects.append(obj)

        self._draw_object_batch(terrain_objects, offset, zoom, screen_w, screen_h)
        draw_units = depth_sorted_draw_units(other_objects)
        self._draw_object_batch(draw_units, offset, zoom, screen_w, screen_h)

        if settings.DISPLAY_SCREEN_CENTER_POINT:
            pygame.draw.circle(self.ui_surface, (255, 0, 0),
                               (screen_w // 2, screen_h // 2), 1)
        if self.world is not None:
            self._draw_collision_shapes()
        if game_mode == GameMode.EDIT and self.editor_manager and hasattr(self.editor_manager,
                                                                          'wall_edit_type') and self.editor_manager.temp_point:
            self._draw_wall_preview()

    # ---------- 静态地形缓冲（只在地形集合变化时重建） ----------
    def _ensure_static_cache(self):
        # 缓存键 = 对象集合版本 + 可见层签名（层显隐切换也需重建，但不改对象版本）
        key = (self.world.geom_revision,
               tuple(b.spec.name for b in self.world.layers if b.spec.visible))
        if self._static_cache_rev == key and self._static_dynamic is not None:
            return

        static = []      # 全部静态地形(Tiled)
        dynamic = []     # 非地形(装饰/Structure/角色)
        for bucket in self.world.layers:
            if not bucket.spec.visible:
                continue
            for obj in bucket.sprites.sprites():
                if isinstance(obj, Tiled):
                    static.append(obj)
                else:
                    dynamic.append(obj)

        verts = []
        segments = []                 # (tex_id, first_quad, quad_count) 连续同纹理段
        cur_tex = None
        run_first = 0
        nq = 0
        for sp in static:
            image = getattr(sp, "image", None)
            wp = getattr(sp, "world_position", None)
            if image is None or wp is None:
                continue
            tex_id = self._get_texture(image)
            if tex_id is None:
                continue
            tw, th = image.get_size()
            if tw == 0 or th == 0:
                continue
            u0, v0 = 0.5 / tw, 1.0 - 0.5 / th
            u1, v1 = 1.0 - 0.5 / tw, 0.5 / th
            cx, cy = wp.center
            hw, hh = wp.width / 2.0, wp.height / 2.0
            x0, y0 = cx - hw, cy - hh
            x1, y1 = cx + hw, cy + hh
            verts.extend((x0, y0, u0, v0, x1, y0, u1, v0, x0, y1, u0, v1,
                          x0, y1, u0, v1, x1, y0, u1, v0, x1, y1, u1, v1))
            if cur_tex is None:
                cur_tex = tex_id
                run_first = nq
            elif cur_tex != tex_id:
                segments.append((cur_tex, run_first, nq - run_first))
                cur_tex = tex_id
                run_first = nq
            nq += 1
        if cur_tex is not None:
            segments.append((cur_tex, run_first, nq - run_first))

        self._static_dynamic = dynamic
        self._static_segments = segments
        self._static_quad_count = nq
        self._static_cache_rev = key

        need_vertices = nq * 6
        if need_vertices == 0:
            return
        if need_vertices > self._static_vbo_cap:
            self._static_vbo_cap = max(self._static_vbo_cap * 2, need_vertices + 1000)
            glBindBuffer(GL_ARRAY_BUFFER, self.static_vbo)
            glBufferData(GL_ARRAY_BUFFER, self._static_vbo_cap * 4 * 4, None, GL_DYNAMIC_DRAW)
        glBindBuffer(GL_ARRAY_BUFFER, self.static_vbo)
        arr = np.array(verts, dtype=np.float32)
        glBufferSubData(GL_ARRAY_BUFFER, 0, arr.nbytes, arr)

    def _draw_static(self, offset, zoom, screen_w, screen_h):
        """整包绘制已缓存的静态地形（不做逐块裁剪；离屏由相机 uniform 在 GPU 侧裁掉）。

        ！必须显式把 u_alpha 设回 1.0：uniform 是【挂在 program 上、跨帧保留】的，
        而静态地形排在动态对象之前绘制、又共用同一个 world_shader。若这里不设，
        上一帧动态批次留下的淡出 alpha（例如 0.35）就会把整片地面按半透明画出来
        —— 表现是"站着不动，地面突然变透且不再恢复"，且只在真的触发过遮挡淡出时
        才出现。任何用 world_shader 的绘制路径都必须自己声明 alpha。
        """
        if self._static_quad_count == 0:
            return
        glUseProgram(self.world_shader)
        glUniform2f(glGetUniformLocation(self.world_shader, "u_screen_size"), screen_w, screen_h)
        glUniform2f(glGetUniformLocation(self.world_shader, "u_camera_offset"), offset[0], offset[1])
        glUniform1f(glGetUniformLocation(self.world_shader, "u_zoom"), zoom)
        glUniform1f(glGetUniformLocation(self.world_shader, "u_alpha"), 1.0)
        glBindVertexArray(self.static_vao)
        glUniform1i(glGetUniformLocation(self.world_shader, "u_texture"), 0)
        for tex_id, start_quad, seg_quads in self._static_segments:
            glActiveTexture(GL_TEXTURE0)
            glBindTexture(GL_TEXTURE_2D, tex_id)
            glDrawArrays(GL_TRIANGLES, start_quad * 6, seg_quads * 6)
        glBindVertexArray(0)

    def _draw_object_batch(self, objects, offset, zoom, screen_w, screen_h, alpha_of=None):
        """绘制对象列表，支持普通精灵和 Structure（经 get_sprite_order 展开）。

        性能优化：整批只 glBufferSubData 一次（不再每个纹理段上传一次），
        再对每个"连续同纹理 + 连续同不透明度"段各发一次 glDrawArrays。
        Structure 深度排序结果已缓存（get_sprite_order），展开不再逐帧重排。

        alpha_of(unit) 给出该单位这一帧的不透明度（遮挡淡出用）。批次必须在
        **不透明度变化处断开** —— 因为顶点里没有颜色通道，u_alpha 是 uniform，
        一次 draw call 只能有一个值。淡出的单位通常整面墙连续，断点很少。
        """
        if not objects:
            return

        u_alpha_loc = glGetUniformLocation(self.world_shader, "u_alpha")

        glUseProgram(self.world_shader)
        glUniform2f(glGetUniformLocation(self.world_shader, "u_screen_size"), screen_w, screen_h)
        glUniform2f(glGetUniformLocation(self.world_shader, "u_camera_offset"), offset[0], offset[1])
        glUniform1f(glGetUniformLocation(self.world_shader, "u_zoom"), zoom)
        glUniform1f(u_alpha_loc, 1.0)
        glBindVertexArray(self.world_vao)
        glBindBuffer(GL_ARRAY_BUFFER, self.world_vbo)

        verts = []        # 扁平 float 顶点数组（每四边形 24 个 float）
        segments = []     # (alpha, tex_id, first_quad_index, quad_count) 连续同(alpha,纹理)段
        cur_key = None    # (alpha, tex_id)
        run_start_quad = 0
        quad_count = 0

        for obj in objects:
            # 每个绘制单位一个不透明度（遮挡淡出）。None -> 全不透明，行为与原来一致。
            unit_alpha_v = 1.0
            if alpha_of is not None:
                try:
                    unit_alpha_v = float(alpha_of(obj))
                except Exception:
                    unit_alpha_v = 1.0
            if unit_alpha_v <= 0.004:
                continue                      # 全透明的不进批次（省顶点）

            if isinstance(obj, Structure):
                sprites = obj.get_sprite_order()
            else:
                sprites = (obj,)
            for sprite in sprites:
                if not sprite.in_active_block:
                    continue
                image = getattr(sprite, "image", None)
                wp = getattr(sprite, "world_position", None)
                if image is None or wp is None:
                    continue
                tex_id = self._get_texture(image)
                if tex_id is None:
                    continue
                tex_w, tex_h = image.get_size()
                if tex_w == 0 or tex_h == 0:
                    continue

                # UV 偏移
                u0, v0 = 0.0 + 0.5 / tex_w, 1.0 - 0.5 / tex_h
                u1, v1 = 1.0 - 0.5 / tex_w, 0.0 + 0.5 / tex_h

                cx, cy = wp.center
                w_world, h_world = wp.width, wp.height
                # 顶点传世界坐标；相机 offset/zoom 由顶点着色器统一变换（见 VERT）
                x0 = cx - w_world / 2
                y0 = cy - h_world / 2
                x1 = x0 + w_world
                y1 = y0 + h_world

                verts.extend((
                    x0, y0, u0, v0,
                    x1, y0, u1, v0,
                    x0, y1, u0, v1,
                    x0, y1, u0, v1,
                    x1, y0, u1, v0,
                    x1, y1, u1, v1,
                ))
                key = (round(unit_alpha_v, 3), tex_id)
                if cur_key is None:
                    cur_key = key
                    run_start_quad = quad_count
                elif cur_key != key:
                    segments.append((cur_key[0], cur_key[1], run_start_quad,
                                     quad_count - run_start_quad))
                    cur_key = key
                    run_start_quad = quad_count
                quad_count += 1

        if not verts:
            glBindVertexArray(0)
            return
        segments.append((cur_key[0], cur_key[1], run_start_quad, quad_count - run_start_quad))

        need_vertices = quad_count * 6
        if need_vertices > self.world_vbo_capacity:
            self.world_vbo_capacity = max(self.world_vbo_capacity * 2, need_vertices + 1000)
            glBufferData(GL_ARRAY_BUFFER, self.world_vbo_capacity * 4 * 4, None, GL_DYNAMIC_DRAW)

        vert_array = np.array(verts, dtype=np.float32)
        glBufferSubData(GL_ARRAY_BUFFER, 0, vert_array.nbytes, vert_array)
        glActiveTexture(GL_TEXTURE0)
        glUniform1i(glGetUniformLocation(self.world_shader, "u_texture"), 0)
        cur_a = None
        for alpha_v, tex_id, start_quad, seg_quads in segments:
            if alpha_v != cur_a:
                glUniform1f(u_alpha_loc, alpha_v)
                cur_a = alpha_v
            glBindTexture(GL_TEXTURE_2D, tex_id)
            glDrawArrays(GL_TRIANGLES, start_quad * 6, seg_quads * 6)
        glBindVertexArray(0)

    # ---------- 端口调试特效（叠加层） ----------
    def _draw_port_markers(self):
        """
        端口特效：绿=空闲、黄=已连接未满、红=已满·占用。
        仅在 settings.DISPLAY_PORT_MARKERS 开启时绘制；
        叠加到 ui_surface，不修改任何精灵的 image（Structure 也不参与绘制）。
        每个端口 = 其所在格子的世界矩形 → 屏幕矩形 → 半透明填充 + 加粗描边。
        """
        if self.world is None:
            return
        if not settings.DISPLAY_PORT_MARKERS:
            return

        camera = self.world.camera
        offset = camera.offset
        zoom = camera.zoom
        screen_w, screen_h = self.screen_size

        markers = []   # (screen_rect, state)  state: 0=绿/1=黄/2=红
        for bucket in self.world.layers:
            if not bucket.spec.visible:
                continue
            objects = bucket.sprites.sprites()
            for obj in objects:
                if not isinstance(obj, Structure):
                    continue
                try:
                    for wp, state in obj.get_port_markers():
                        # 与 _draw_object_batch 同一套世界→屏幕换算
                        left = (wp.left - offset[0]) * zoom
                        top = (wp.top - offset[1]) * zoom
                        rect = pygame.Rect(left, top, wp.width * zoom, wp.height * zoom)
                        # 屏幕裁剪
                        if rect.right < 0 or rect.bottom < 0:
                            continue
                        if rect.left > screen_w or rect.top > screen_h:
                            continue
                        markers.append((rect, state))
                except Exception:
                    continue

        # state -> 填充 & 边框配色（0=空闲绿 / 1=已连未满黄 / 2=已满占用红）
        fill_map = {
            0: (120, 255, 90, 70),
            1: (255, 240, 90, 70),
            2: (255, 90, 90, 70),
        }
        border_map = {
            0: (0, 230, 80, 255),
            1: (255, 215, 0, 255),
            2: (230, 40, 40, 255),
        }

        for rect, state in markers:
            fill = fill_map.get(state, fill_map[0])
            border = border_map.get(state, border_map[0])
            layer = pygame.Surface(rect.size, pygame.SRCALPHA)
            layer.fill(fill)
            self.ui_surface.blit(layer, rect.topleft)
            pygame.draw.rect(self.ui_surface, border, rect, 3)

    # ---------- CPU 辅助绘制 ----------
    def _draw_wall_preview(self):
        points = self.editor_manager.wall_points
        temp = self.editor_manager.temp_point
        screen_points = [self.world.camera.world_to_screen(p) for p in points]
        temp_screen = self.world.camera.world_to_screen(temp)

        if self.editor_manager.wall_edit_type == 'polygon':
            for pt in screen_points:
                pygame.draw.circle(self.ui_surface, (0, 255, 0), (int(pt[0]), int(pt[1])), 5)
            if len(screen_points) > 1:
                pygame.draw.lines(self.ui_surface, (0, 255, 0), False, screen_points, 2)
            if screen_points:
                pygame.draw.line(self.ui_surface, (0, 255, 0), screen_points[-1], temp_screen, 2)
        elif self.editor_manager.wall_edit_type == 'circle':
            if screen_points:
                center = screen_points[0]
                radius = math.hypot(temp_screen[0]-center[0], temp_screen[1]-center[1])
                pygame.draw.circle(self.ui_surface, (0, 255, 0), (int(center[0]), int(center[1])), int(radius), 2)
        elif self.editor_manager.wall_edit_type == 'rectangle':
            if screen_points:
                start = screen_points[0]
                rect = pygame.Rect(min(start[0], temp_screen[0]), min(start[1], temp_screen[1]),
                                   abs(temp_screen[0]-start[0]), abs(temp_screen[1]-start[1]))
                pygame.draw.rect(self.ui_surface, (0, 255, 0), rect, 2)

    def _draw_collision_shapes(self):
        if not hasattr(self.world, 'physics'):
            return
        space = self.world.physics.space
        if space is None:
            return

        camera = self.world.camera
        for body in space.bodies:
            shapes_vertices = get_body_collision_vertices(body, circle_segments=20)
            for verts in shapes_vertices:
                screen_verts = [camera.world_to_screen((x, y)) for (x, y) in verts]
                screen_ints = [(int(v[0]), int(v[1])) for v in screen_verts]
                if len(screen_ints) == 2:
                    pygame.draw.line(self.ui_surface, (0, 255, 0), screen_ints[0], screen_ints[1], 1)
                elif len(screen_ints) >= 3:
                    pygame.draw.polygon(self.ui_surface, (0, 255, 0), screen_ints, 1)