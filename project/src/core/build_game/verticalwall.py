"""verticalwall.py —— 竖木墙（VerticalWall / VerticalWoodWall）(S4：拆继承后)

竖墙与横墙 shape/尺寸/朝向本质不同，直接继承 Structure 并混入 WallGridMixin，
各自实现 shape/网格/朝向；不再继承 HorizontalWall。仅在必要时共享墙族角端口词汇。
"""

import pygame

from src.core.build_game.structure import Structure, RenderOrder
from core.build_game.grid.wall_grid import WallGridMixin
from src.core.build_game.tiledshape import VerticalTiledRect
from src.core.build_game.port import Port
from src.core.build_game.vertical_texture import vertical_cell_frame
from src.core.build_game.wall_spec import VWALL_SPEC, WallSpec

# 端口名 / 语义名 → 头尾关键字（贯通判定用）
_END_ALIAS = {"top": "head", "head": "head", "tail": "tail"}


def wall_kind_of(struct) -> str:
    """结构是不是"墙"：竖墙返回 "v"、横墙返回 "h"、其它 None（只做观测/判定）。"""
    st = getattr(struct, "structure_type", None)
    if st == "vertical_wall":
        return "v"
    if st == "horizontal_wall":
        return "h"
    return None


def _wall_center_x(wall) -> float:
    """墙（或组合结构）的中线 X：竖墙用它判"是不是同一列"。"""
    shape = getattr(wall, "shape", None)
    if shape is not None:
        try:
            return float(shape.centerx)
        except Exception:      # noqa: BLE001
            pass
    try:
        return float(getattr(wall, "centerx", 0.0) or 0.0)
    except Exception:          # noqa: BLE001
        return 0.0


class VerticalWallPort(Port):
    """竖墙侧边多向节点：位于 1 列竖墙的某一格，可沿 left/right 两个方向连接。

    竖墙的“头/尾/底”格在同一格的不同连接方向上属于墙族不同的角词汇
    （如头格左缘=TopLeft、右缘=TopRight），因此覆写 edge_type(side)：
    查规则前先按 side 归一化到对应的墙族角类型。
    """
    port_type = "VerticalWall"
    connectable_sides = ("left", "right")
    # 竖墙角口：左右两个方向是【两个角色】（左边一堵墙 / 右边一堵墙），各占一条边。
    # 容量取方向数 = 2，不再压成 1 —— 拆边补出来的 1 格宽竖墙段，那一格常常
    # 左边顶着房子的墙、右边顶着走廊的墙，压成 1 就会有一边永远接不上
    # （表现："竖墙一边没对准"、"下半的连接没有"）。
    # 代价：只连了一边的角口底色从"红(已满)"变成"黄(未满)"，两边都连满才是红。
    max_connections = None       # None = 缺省取 len(connectable_sides) = 2

    def edge_type(self, side: str) -> str:
        row = self.cell[1]
        wall = self.structure
        # 切段（mask 过的墙）：两端都按"头/尾段"词汇走。
        # 段的两头接的是别的【墙】，不是地板 —— 底格词汇 HorizontalWallBottom*
        # 是"连地板"用的，段上用它会查不到对应规则（或接错对象）。
        if getattr(wall, "is_slice", None) and row in wall.visible_rows():
            return "HorizontalWallTopLeft" if side == "left" else "HorizontalWallTopRight"
        L = getattr(wall, "_v_length", None)
        gh = getattr(wall, "_grid_height", None)
        if L is not None and row in (0, L - 1):
            # 头 / 尾段：归一到 Top 类角词汇
            return "HorizontalWallTopLeft" if side == "left" else "HorizontalWallTopRight"
        if gh is not None and row == gh - 1:
            # 底格：归一到 Bottom 类角词汇
            return "HorizontalWallBottomLeft" if side == "left" else "HorizontalWallBottomRight"
        return self.type


class VerticalWall(WallGridMixin, Structure):
    """
    竖墙（无纹理壳）。宽(厚度)恒为 1；逻辑尺寸 = 长(L) × 高(H)。
    屏幕网格 = 1 列 × (长+高) 行。
    四角端口词汇沿用横墙 —— 同属“墙族”，规则一份即可互通。
    """

    structure_type = "vertical_wall"

    # 行 → 段语义的声明（见 wall_spec.py）。_map()/_cell_type_at() 都由它推导。
    WALL_SPEC: WallSpec = VWALL_SPEC

    def __init__(self, length: int, height: int,
                 cell_size=(64, 64),
                 center=(0, 0),
                 layer_offset: int = RenderOrder.MIDDLE.value):
        rows = int(length) + int(height) - 1    # 前 length 行顶段 + 后 height-1 行侧段
        if rows < 2:
            raise ValueError("竖墙 长+高-1 至少为 2 行")
        super().__init__(layer_offset)
        self._grid_width, self._grid_height = 1, rows
        self._cell_w, self._cell_h = cell_size
        self.shape = VerticalTiledRect(int(length), int(height), center)
        self._v_length = int(length)
        self._v_height = int(height)

        self.orientation = "left"        # 默认左套朝向
        self._head_connected = False
        self._head_right = False
        self._tail_connected = False
        self._tail_connect_right = False
        self._tail_cross = False
        self._head_cross = False
        # 头格"上面还有同一堵墙"（走廊串联 / 走廊接到房子）：头格改画跑通纹，
        # 不画自由端收口（见 vertical_texture.vertical_cell_frame 的 head_run）
        self._head_run = False
        # 尾格同理：共线续接时，尾段也是"一堵长墙的中间"，画贯通纹而不是转角纹
        self._tail_run = False
        # 共线续接的邻居记录 {head/tail: 另一堵墙}（见 wall_joint.note_inline）：
        # 端口那头不一定连得上（比如房子侧墙的头已被北墙占满），但"接上了"是事实
        self._inline_notes = {}
        # 开口接上了别的结构（走廊专用，见 note_opening_attached）
        self._head_attached = False
        self._tail_attached = False
        # 调用方强制指定贯通（None = 不干预，交给自动判定）
        self._run_override = {"head": None, "tail": None}

        # ---- 侧面填充：让墙有可见厚度（默认【关】，画面零变化） ----
        # 实测（tests/probe_wall_art.py）：竖墙每格 surface 是 64×64，但贴图只画了
        # 半格 32px，另一半是纯空的 —— 所以墙看起来只有半格宽、没有厚度。
        #   side_face = None            -> 关（默认）
        #   side_face = "44"            -> 用 House_44 填那半格（也可直接给 Surface）
        #   side_face_fill = 1.0        -> 填满空半格（墙宽翻倍）
        #                  0.5          -> 只填一半（"稍微宽一点点"）
        self.side_face = None
        self.side_face_fill = 1.0

        self.sprite_types = {
            "top": pygame.sprite.Sprite,
            "middle": pygame.sprite.Sprite,
            "bottom": pygame.sprite.Sprite,
        }
        self.sprite_types["top_run"] = self.sprite_types["top"]
        self.sprite_types["top_tail"] = self.sprite_types["top"]

    @property
    def v_length(self) -> int:
        return self._v_length

    @property
    def v_height(self) -> int:
        return self._v_height

    def is_slice(self) -> bool:
        """这堵墙是不是"从一整面墙上切下来的一段"（挖过格、可见范围不覆盖整墙）。

        用途：切段的端口应落在**它自己的两个端点**上，而不是原墙的 0 / L-1 / gh-1
        —— 那三行未必还在段里。没挖过格的墙（包括无纹理壳、一个精灵都没有的
        VerticalWall）visible_rows() 就是整墙范围，恒为 False，行为与原来一致。
        """
        return self.visible_rows() != (0, int(self._grid_height) - 1)

    def _map(self):
        """行映射：由 WALL_SPEC 声明推导（段名同时就是 sprite_types 的 key）。"""
        return self.WALL_SPEC.rules(self._v_length, self._v_height)

    def _cell_type_at(self, gy: int) -> str:
        """行号 -> 段语义名：与 _map() 同源于一份声明，不会漂移。"""
        return self.WALL_SPEC.segment_at(gy, self._v_length, self._v_height)

    # 整根竖墙作为【一个整体】参与深度排序时，墙脚线取哪一行：
    #   "tail" = 地面段末端行（默认）—— 竖墙占地（沿地面南北铺开的那一段）的南缘，
    #    "tail" = 地面段末端行（占地段的南缘）—— 墙"踩在地上"的位置。
    #             但视觉上墙一直画到侧立面底段，用这条会显得墙比实际浅。
    #    "bbox" = 整墙包围盒下缘 = 贴图的最下缘（**视觉上的最下面**）—— 当前值。
    DEPTH_BASE_ROW = "bbox"

    def depth_base_y(self) -> float:
        """整根竖墙的墙脚线 = 贴图的最下缘（视觉上的最下面）。

        竖墙是【一个整体】：沿地面铺开的横段 + 南端的侧立面，共用一条墙脚线，
        和横墙一样整面一起参与排序 —— 不能逐行拆开判断，否则同一堵墙会一半
        在实体身后、一半在实体身前，看起来"墙被劈成了两半"。

        取"视觉最下缘"而不是"占地段南缘"：墙画到哪里，玩家就认为它站到哪里。
        占地段（tail 行）只是它沿地面铺开的那一段，侧立面底段同样在视觉上占据
        更靠南的位置，若忽略它，墙会跟它下方的实体抢错前后关系。
        """
        if str(self.DEPTH_BASE_ROW).lower() == "tail":
            return self._row_bottom_y(int(self._v_length) - 1)
        return super().depth_base_y()

    def port_node_for_cell(self, cell: tuple[int, int]):
        """1 列 × N 行竖墙：顶格/顶段末行/底格带可连节点（左右两侧皆可）。

        切段（mask 过的墙）例外：可连节点落在**可见范围的两个端点行**上 ——
        原墙的 0 / L-1 / gh-1 那三行未必还在段里。极端情况：一段只剩一行时，
        唯一挂着的行恰好是原墙的"墙脚"行（gh-1），于是段上只剩一个连地板用的
        bottom 口，两旁的墙永远接不上，纹理就停在"自由端"（看起来像断了）。
        """
        if self.is_slice():
            lo, hi = self.visible_rows()
            return VerticalWallPort if int(cell[1]) in (lo, hi) else None
        row = cell[1]
        if row in (0, self._v_length - 1, self._grid_height - 1):
            return VerticalWallPort
        return None

    def port_name_for(self, side: str, index: int, cell: tuple[int, int]):
        """竖墙三段语义名：top(头) / tail(尾段) / bottom(底)。

        同一格在 left/right 两侧各有方向实例，`ports` 字典按「语义名 → 该侧实例」收录；
        这里的名不带方向（top/tail/bottom 与贴哪侧无关），需要贴哪侧由连接方决定。

        切段：名字落在可见范围的两个端点行上（北端 = top，南端 = tail）。
        单行切片（`lo == hi`）例外：那一行**既是段的头也是段的尾**，两个名字都得
        登记 —— `ports` 是按 (side, cell) 逐条登记的，只给一个名字的话
        `wall_end_ports` 就只看得到 "top"，尾端整端接不上
        （表现：生成的竖墙 tail 上没有 port、尾部也没有连接）。
        """
        if self.is_slice():
            lo, hi = self.visible_rows()
            row = int(cell[1])
            if lo == hi:
                # 同一格登记两次：从左边看叫 top，从右边看叫 tail。
                return "top" if side in ("top", "left") else "tail"
            if row == lo:
                return "top"
            if row == hi:
                return "tail"
            return None
        L = self._v_length
        row = cell[1]
        if row == 0:
            return "top"
        if row == L - 1:
            return "tail"
        if row == self._grid_height - 1:
            return "bottom"
        return None

    def _sprite_at_port(self, port):
        return self.get_sprite_at_grid(port.cell)

    # ---- 侧面填充：把格子贴图空着的那半补上侧脸纹 ----
    def _side_tile_surface(self):
        """取填充用的贴图（side_face 可为 House 编号字符串，或现成 Surface）。"""
        tile = self.side_face
        if tile is None:
            return None
        if isinstance(tile, pygame.Surface):
            return tile
        try:
            from src.core.resource_manager import get_surface_by_id
            return get_surface_by_id(f"terrain/graphics/environment/House_{tile}")
        except Exception:
            return None

    def compose_side_face(self, base: pygame.Surface):
        """把 base（原生半格贴图）与侧脸片拼成一张整格贴图。

        规则（不猜美术，全部由数据决定）：
          - 侧脸只贴在 base【空着的那半格】里；base 若已占满整格则不拼。
          - 侧脸片的**高度与底边对齐 base 的实际绘制高度/底边** —— 这样未连接的
            顶格（实测只有 20px 高的小段）不会因为补了侧脸而突然变得比正脸高。
          - 侧脸片紧贴正脸摆放（读作"墙的厚度长在这里"）。
        拼不了就返回 None（调用方保持原图，绝不产生半成品）。
        """
        side = self._side_tile_surface()
        if side is None:
            return None
        try:
            W, H = base.get_size()
            art = base.get_bounding_rect()
        except Exception:
            return None
        if art.width <= 0 or art.height <= 0:
            return None

        # 哪半边空着？侧脸【紧贴正脸】往空的那侧长，保证轮廓连续（中间不留缝）。
        # 整格贴图无需补。
        half = W // 2
        frac = float(self.side_face_fill or 0)
        if art.left >= half:                 # 正脸在右半 -> 侧脸贴它左侧，向左长
            avail = art.left
            fill = max(1, min(avail, int(round(avail * frac))))
            x = art.left - fill
        elif art.right <= half:              # 正脸在左半 -> 侧脸贴它右侧，向右长
            avail = W - art.right
            fill = max(1, min(avail, int(round(avail * frac))))
            x = art.right
        else:
            return None                      # 正脸已占满整格，无需补
        if fill <= 0 or x < 0 or x + fill > W:
            return None

        # 侧脸片按 base 的实际绘制范围裁剪后缩放
        try:
            sbr = side.get_bounding_rect()
            if sbr.width <= 0 or sbr.height <= 0:
                return None
            piece_size = (fill, art.height)
        except Exception:
            return None

        # 走 resource_manager 的 compose_surfaces：这次拼接会登记"配方"，
        # 于是存档里只写（母图 + 侧脸片来源 + 裁剪/缩放/位置），而不是这张合成图
        try:
            from src.core.resource_manager import compose_surfaces
            return compose_surfaces(base, [{
                'src': side,
                'crop': (sbr.x, sbr.y, sbr.width, sbr.height),
                'scale': piece_size,
                'pos': (x, art.top),
            }])
        except Exception:
            return None

    def set_side_face(self, tile, fill: float = 1.0):
        """声明侧面填充并**立即生效**；tile=None 表示关掉（恢复纯几何默认）。

        返回 self，便于链式写法：``VWall(5, 3).set_side_face("49")``。
        """
        self.side_face = tile
        self.side_face_fill = float(fill)
        for sprite in self.iter_sorted_leaf_sprites():
            self._decorate_leaf(sprite)
        return self

    def _decorate_leaf(self, sprite) -> None:
        """收口：按声明给格子补侧脸。side_face 为 None 时**什么都不做**。"""
        if self.side_face is None:
            return None
        base = getattr(sprite, "_base_surf", None)
        if base is None:
            # 精灵没暴露原始面：只有当前图不是我们自己拼过的，才敢拿它当原始面
            img = getattr(sprite, "image", None)
            if img is None or img is getattr(sprite, "_side_face_image", None):
                return None
            base = img

        key = (base, str(self.side_face) if not isinstance(self.side_face, pygame.Surface)
               else id(self.side_face), float(self.side_face_fill or 0))
        cached = getattr(sprite, "_side_face_key", None)
        if cached == key and getattr(sprite, "_side_face_image", None) is not None:
            composed = sprite._side_face_image
        else:
            composed = self.compose_side_face(base)
            if composed is None:
                return None
            try:
                sprite._side_face_key = key
                sprite._side_face_image = composed
            except Exception:
                pass
        if getattr(sprite, "image", None) is not composed:
            sprite.image = composed
        return None

    def wall_texture_plan(self) -> list:
        """Structure 自决策视图：返回整墙每行 (gy, cell_type, (id,flip))。"""
        gh = self._grid_height
        ori = getattr(self, "orientation", "left")
        head_conn = bool(getattr(self, "_head_connected", False))
        head_right = bool(getattr(self, "_head_right", False))
        hcross = bool(getattr(self, "_head_cross", False))
        hrun = bool(getattr(self, "_head_run", False))
        tconn = bool(getattr(self, "_tail_connected", False))
        trun = bool(getattr(self, "_tail_run", False))
        tright = bool(getattr(self, "_tail_connect_right", False))
        # 注意属性名是 _tail_cross（on_topology_settled 里按 "朝向 != 尾端口侧" 算出来的）。
        # 这里曾写成 _tail_crossed（多了个 d），getattr 默认 False → 跨半格那一档永远
        # 选不到 7/15、41/55、42/63，全部退回贴合纹。修正后与 _apply_vertical_frame
        # 的旧口径（读 _tail_cross）一致。
        tcross = bool(getattr(self, "_tail_cross", False))
        plan = []
        for gy in range(gh):
            ct = self._cell_type_at(gy)
            # 墙顶补厚度那一段：头格下面 height-1 格（头格自己由 head_cross 管）
            cross_run = hcross and not hrun and 1 <= gy <= int(self._v_height) - 1
            idf = vertical_cell_frame(ct, orientation=ori,
                                      head_right=head_conn and head_right,
                                      head_left=head_conn and not head_right,
                                      # 尾段"连着但共线"→ 当贯通处理
                                      tail_connected=tconn and not trun, tail_right=tright,
                                      tail_cross=tcross, head_cross=hcross,
                                      head_run=hrun, cross_run=cross_run)
            plan.append((gy, ct, idf))
        return plan

    def set_head_run(self, on: bool = True, *, force: bool = True):
        """声明本墙的【头格】上面还有同一堵墙（跑通），并重算贴图。

        用于走廊串联 / 走廊接房子：那一端已经不是"自由端"，头格不该出现收口纹
        （33 / 8 / 10 是"这里到头了"的语义），改用与 top_run 相同的贯通纹。
        force=True（默认）= 强制覆盖（mark_junction 这类手工声明用）；force=False
        只改当前值，下一次拓扑重算会被自动判定覆盖。
        """
        on = bool(on)
        if force:
            self._run_override["head"] = on
        self.refresh()
        return self

    def set_tail_run(self, on: bool = True, *, force: bool = True):
        """声明本墙的【尾格】也是"一堵长墙的中间"（共线续接），并重算贴图。"""
        on = bool(on)
        if force:
            self._run_override["tail"] = on
        self.refresh()
        return self

    def clear_run_overrides(self):
        """清掉强制覆盖，回到自动判定（调用方随后自行 refresh）。"""
        for end in ("head", "tail"):
            self._run_override[end] = None
        return self

    def note_inline(self, end: str, peer=None):
        """记下"这一端与另一堵墙共线续接"（wall_joint 用；不依赖端口能否连上）。

        :param end: 端口名（"top"/"tail"）或语义名（"head"/"tail"）
        :param peer: 对端墙；None = 抹掉这条记录
        """
        key = _END_ALIAS.get(str(end).lower())
        if key is None:
            return self
        if peer is None:
            self._inline_notes.pop(key, None)
        else:
            self._inline_notes[key] = peer
        return self

    def note_opening_attached(self, end: str, on: bool = True):
        """记下"这一端的开口接上了别的结构"（走廊专用；不一定是墙端对墙端）。

        判定优先级见 :meth:`_resolve_run`：那一端真连到一堵垂直的墙（转角）时转角纹
        优先；否则按贯通处理 —— 开口接上了就不该再画自由端收口。
        """
        key = _END_ALIAS.get(str(end).lower())
        if key == "head":
            self._head_attached = bool(on)
        elif key == "tail":
            self._tail_attached = bool(on)
        return self

    # ---- 贯通判定的零件 ----
    @staticmethod
    def _peer_struct(port):
        if port is None:
            return None
        for peer in (getattr(port, "peers", {}) or {}).values():
            return getattr(peer, "structure", None)
        return None

    def _collinear_with(self, peer) -> bool:
        """对端是不是"同一列的另一堵竖墙"（共线续接）。"""
        if getattr(peer, "structure_type", None) != "vertical_wall":
            return False
        return abs(_wall_center_x(peer) - _wall_center_x(self)) < 0.5

    def _resolve_run(self, end: str, port) -> bool:
        """这一端（head/tail）该不该画【贯通纹】。

        优先级（先到先得）：
          1. 调用方强制覆盖（mark_junction / set_head_run，见 _run_override）；
          2. 端口连着一堵【不共线】的墙（成角 corner / 并排端接 side_by）→
             交给既有转角逻辑（不贯通：头画 8/10/22，尾画 24/26/15/7）；
          3. 端口连着另一堵【共线】的墙 / 有共线邻居记录 → 贯通（16/18）；
          4. 本端开口接上了别的结构（走廊专用）→ 贯通（不再画自由端收口）。
        """
        forced = (getattr(self, "_run_override", {}) or {}).get(end)
        if forced is not None:
            return bool(forced)
        peer = self._peer_struct(port)
        if peer is not None and wall_kind_of(peer) is not None and not self._collinear_with(peer):
            return False                      # 真接上了一堵墙（成角 / 并排端接）
        if (peer is not None and self._collinear_with(peer)) \
                or (getattr(self, "_inline_notes", {}) or {}).get(end) is not None:
            return True
        return bool(getattr(self, "_head_attached" if end == "head" else "_tail_attached", False))

    def cell_in_cross_span(self, sprite) -> bool:
        """这一格在不在【墙顶补厚度那一段】里（头格下面 height-1 格）。

        跨半格时墙顶要连着补 height 格：头格自己一格（用 head 那一档的跨半格纹），
        往下 height-1 格用 top_mid_cross。再往下回到普通贯通纹 ——
        只补头格看不出厚度，补整根又会糊。

        头格已经按【贯通】画了（head_run：上面还接着墙）时就不再补这段：
        否则会出现"头是贯通纹、下面两格突然变厚"的断层。
        """
        if not bool(getattr(self, "_head_cross", False)):
            return False
        if bool(getattr(self, "_head_run", False)):
            return False
        cell = self._cell_of_leaf(sprite)
        if cell is None:
            return False
        gy = int(cell[1])
        return 1 <= gy <= int(self._v_height) - 1

    def refresh_runs(self):
        """按当前端口 / 邻居记录重算头尾的贯通标记，并重算贴图。

        这是 :meth:`on_topology_settled` 的**薄别名** —— 墙的贴图只有一个重算入口
        （读全端口簇 → 算朝向/头尾连接态/贯通态 → 逐格换图）。保留本名字是因为
        `hall.sync_junctions` 与 `wall_joint` 用它，语义上确实只是"重算贯通"。
        """
        self.on_topology_settled()
        return {"head_run": bool(self._head_run), "tail_run": bool(self._tail_run)}

    def refresh(self):
        """整墙重算的唯一公开入口（= on_topology_settled 的语义别名）。"""
        self.on_topology_settled()
        return self

    def recompute(self):
        """自决策：读当前拓扑状态，把整墙贴图**一次性**发给每格精灵。

        为什么改成"墙算完、精灵照贴"：以前这里是 if/elif 分派 —— 按格子类型去调精灵的
        `on_wall_dock` / `set_connected` / `set_run_through` 三个不同方法，每个方法内部
        又把墙的状态读回去算一遍。判定逻辑因此分散在"墙"和"精灵"两处，同一件事有两种
        写法（见 CALLBACK_SURFACE.md §7.1）。

        现在只有一条路径：
            wall_texture_plan()（判定的唯一来源）→ 每格 (cell_type, (帧号, 翻转))
            → 精灵 apply_frame(帧号, 翻转)
        `wall_texture_plan()` 与本方法此前的结果**逐格等价**
        （核对脚本：tests/_probe_plan_equiv.py，8 组拓扑状态 0 处不一致）。
        """
        for gy, ct, (fid, flip) in self.wall_texture_plan():
            sprite = self.get_sprite_at_grid((0, gy))
            if sprite is None:
                continue
            apply_frame = getattr(sprite, "apply_frame", None)
            if callable(apply_frame):
                apply_frame(fid, flip)
            # 精灵换完图后的收口：补侧脸（side_face 关时无操作）
            self._decorate_leaf(sprite)
        return self.wall_texture_plan()

    def on_topology_settled(self):
        """整批端口变化后重算本墙的朝向 / 头尾连接态 / 贯通态，再刷新整墙贴图。"""
        L = self._v_length
        gh = self._grid_height
        # 切段（mask 过的墙）：端口落在**它自己的两个端点行**上（见 is_slice）。
        # 若还按原墙的 0 / L-1 / gh-1 去认，连上的 tail 会被当成"底格(连地板)"，
        # tail_connected 永远是 False —— 贴图就停在自由端，怎么连都没反应。
        if self.is_slice():
            lo, hi = self.visible_rows()
            head_cell, tail_cell, bottom_cell = (0, lo), (0, hi), None
        else:
            head_cell, tail_cell, bottom_cell = (0, 0), (0, L - 1), (0, gh - 1)
        head_p = tail_p = bottom_p = None
        for p in self.connected_ports():
            if p.cell == head_cell:
                head_p = p
            elif p.cell == tail_cell:
                tail_p = p
            elif bottom_cell is not None and p.cell == bottom_cell:
                bottom_p = p

        # ---- 朝向：由"已连上的那一端"相对对端的位置决定（先到先得） ----
        # 优先级：底口(地板) → 任何"与本体不同列/不同行"的 peer（才判得出左右）
        #         → 剩下的共线 peer。
        # 为什么底口优先、并且要跳过共线的 peer：
        #   走廊的墙头现在也会连到别的墙上（共线续接 / 成角），而共线的 peer 与本墙
        #   同列，用它去比 centerx 是判不出左右的（会把左墙的贴图套到右墙上）。
        #   地板/底口一定指向本结构内侧，是判朝向最可靠的那一端；
        #   独立墙（没有地板可连）仍按原来的 头→尾→底 顺序退让，行为不变。
        def _lateral(p):
            peer = next(iter(p.peers.values())).structure
            px = getattr(peer, "centerx", None)
            return px is not None and abs(float(px) - float(self.centerx)) > 1e-6

        def _ref_port():
            if bottom_p is not None and bottom_p.peers and _lateral(bottom_p):
                return bottom_p
            for p in (head_p, tail_p, bottom_p):
                if p is not None and p.peers and _lateral(p):
                    return p
            for p in (head_p, tail_p, bottom_p):
                if p is not None and p.peers:
                    return p
            return None

        ref = _ref_port()
        if ref is not None:
            peer = next(iter(ref.peers.values())).structure
            px = getattr(peer, "centerx", None)
            if px is not None:
                self.orientation = "right" if self.centerx < px else "left"

        # ---- 头部 ----
        if head_p is not None and head_p.peers:
            self._head_connected = True
            self._head_right = (head_p.side == "right")  # 端口侧
            # cross = 朝向与端口侧相反 → 跨半格
            self._head_cross = (self.orientation != head_p.side)
        else:
            self._head_connected = False
            self._head_right = False
            self._head_cross = False
            if not hasattr(self, 'orientation'):
                self.orientation = "left"

        # ---- 尾部 ----
        if tail_p is not None:
            self._tail_connected = True
            self._tail_connect_right = (tail_p.side == "right")
            self._tail_cross = (self.orientation != tail_p.side)  # 修正为 !=
        else:
            self._tail_connected = False
            self._tail_connect_right = False
            self._tail_cross = False

        # ---- 贯通（共线续接）：头/尾接的是"同一列的另一堵墙"时不画墙头/转角 ----
        self._head_run = self._resolve_run("head", head_p)
        self._tail_run = self._resolve_run("tail", tail_p)

        # ---- 切段接到【横墙】上时按"跨半格"处理 ----
        # 房子/走廊的横墙是 overlap_bottom 摆法、底行紧贴地板行；竖墙段向外靠了
        # 一格之后，与这条"墙贴着地板"的约定差半格 —— 所以头/尾要走 cross 那一档，
        # 透视才和走廊自己的墙一致（见 house_edge._push_hall_outward）。
        # 平移一格不会改变 _head_cross（它是"朝向 vs 端口侧"的相对关系），
        # 所以这一档必须显式声明，不能指望几何自己算出来。
        if self.is_slice():
            for end, port in (("head", head_p), ("tail", tail_p)):
                if port is None:
                    continue
                peer = next(iter((getattr(port, "peers", {}) or {}).values()), None)
                if peer is None:
                    continue
                if wall_kind_of(getattr(peer, "structure", None)) == "h":
                    if end == "head":
                        self._head_cross = True
                    else:
                        self._tail_cross = True

        self.recompute()
        return self.wall_texture_plan()

    def on_port_changed(self, port_a: Port, port_b: Port, side_a: str, side_b: str,
                        *, connected: bool = True):
        # 竖向墙的换图是"读整批拓扑 → 自决策"，统一由 on_topology_settled 承担
        # （引擎在事务结束时调用一次）。这里只做逐对收口，不重复重算。
        super().on_port_changed(port_a, port_b, side_a, side_b, connected=connected)

    def debug_self(self) -> str:
        """运行时自检：打印朝向/头尾连接 + 每格当前实际 House 纹理。"""
        from src.game import entity as E
        cand = ["40", "41", "42", "8", "10", "16", "18", "24", "26",
                "7", "15", "45", "33", "44", "43", "22", "23", "55", "47", "63", "39"]
        lines = [f"{type(self).__name__} orient={self.orientation} "
                 f"head_conn={self._head_connected}(right={self._head_right}) "
                 f"tail_conn={self._tail_connected}(right={self._tail_connect_right},"
                 ]
        for gy in range(self._grid_height):
            s = self.get_sprite_at_grid((0, gy))
            ct = self._cell_type_at(gy)
            label = "?"
            if s is not None and getattr(s, "_base_surf", None) is not None:
                for h in cand:
                    if s._base_surf is E._vw_surface(h):
                        label = h
                        break
                    if s._base_surf is E._vw_surface(h, True):
                        label = h + "*"
                        break
            lines.append(f"  row{gy:2d} {ct:11s} -> House_{label}")
        return "\n".join(lines)


class VerticalWoodWall(VerticalWall):
    """竖木墙：签名 (长, 高, center)。精灵类在 entity 定义，连上后由 recompute 换图。"""

    def __init__(self, length: int, height: int, center=(0, 0)):
        VerticalWall.__init__(self, length, height, (64, 64), center)
        from src.game.entity import (VerticalWoodWallTiledTop,
                                     VerticalWoodWallTiledTopRun,
                                     VerticalWoodWallTiledTopTail,
                                     VerticalWoodWallTiledMiddle,
                                     VerticalWoodWallTiledBottom)
        self.sprite_types["top"] = VerticalWoodWallTiledTop
        self.sprite_types["top_run"] = VerticalWoodWallTiledTopRun
        self.sprite_types["top_tail"] = VerticalWoodWallTiledTopTail
        self.sprite_types["middle"] = VerticalWoodWallTiledMiddle
        self.sprite_types["bottom"] = VerticalWoodWallTiledBottom
        self.parse_and_register(config=self._map(), sprite_factory=self.sprite_factory)

    def sprite_factory(self, typ):
        return self.sprite_types[typ]()


def register_wall_family_corner_rules(log=None):
    """注册墙族(横墙/竖墙共用)角 Port 规则 —— 委托给 wall_rules 的声明表。

    规则本身写在 wall_rules.WALL_FAMILY_RULES（一份数据），本函数只负责"装上"。
    幂等：该类型对已注册就直接复用，不重复注册、不报错（原因见 wall_rules.install）。
    """
    from src.core.build_game.wall_rules import WALL_FAMILY_RULES, install
    return install(WALL_FAMILY_RULES, log=log)
