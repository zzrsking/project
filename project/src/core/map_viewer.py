import pygame
import sys

from pytmx import pytmx
from pytmx.util_pygame import load_pygame
from os import walk
from pathlib import Path

# ==================== 配置 ====================
SCREEN_WIDTH = 800
SCREEN_HEIGHT = 600
TILE_SIZE = 64
WATER_ANIMATION_SPEED = 5.0

# ==================== 资源路径 ====================
def get_resource_path(relative_path: str) -> str:
    for p in Path(__file__).resolve().parents:
        if (p / "assets").exists():
            return str(p / "assets" / relative_path)
    raise FileNotFoundError("找不到 assets 目录")

def import_images_from_folder(path):
    """加载文件夹内所有图片，返回 Surface 列表"""
    surfaces = []
    try:
        for _, _, files in walk(path):
            for f in files:
                surfaces.append(pygame.image.load(path + '/' + f).convert_alpha())
    except FileNotFoundError:
        pass
    return surfaces

# ==================== 精灵类 ====================
class Generic(pygame.sprite.Sprite):
    def __init__(self, pos, surf, groups, z, layer_name):
        super().__init__(groups)
        self.image = surf
        self.rect = self.image.get_rect(topleft=pos) if self.image else pygame.Rect(pos, (0, 0))
        self.z = z
        self.layer_name = layer_name

class Water(Generic):
    def __init__(self, pos, frames, groups, z, layer_name, speed=WATER_ANIMATION_SPEED):
        self.frames = frames
        self.frame_index = 0.0
        self.speed = speed
        super().__init__(pos, frames[0] if frames else None, groups, z, layer_name)

    def update(self, dt):
        if not self.frames:
            return
        self.frame_index += self.speed * dt
        self.frame_index %= len(self.frames)
        self.image = self.frames[int(self.frame_index)]

# ==================== 相机组（含绘制逻辑） ====================
class CameraGroup(pygame.sprite.Group):
    def __init__(self):
        super().__init__()
        self.display_surface = pygame.display.get_surface()
        self.offset = pygame.math.Vector2()

    def custom_draw(self, layer_visibility):
        sorted_sprites = sorted(self.sprites(), key=lambda s: (s.z, s.rect.centery))
        for sprite in sorted_sprites:
            if not layer_visibility.get(sprite.layer_name, True):
                continue
            if sprite.image is None:
                continue
            offset_rect = sprite.rect.copy()
            offset_rect.topleft -= self.offset
            self.display_surface.blit(sprite.image, offset_rect)

# ==================== 输入框（图层切换） ====================
class InputBox:
    def __init__(self, x, y, w, h, font, layer_list):
        self.rect = pygame.Rect(x, y, w, h)
        self.color_inactive = pygame.Color('gray')
        self.color_active = pygame.Color('white')
        self.color = self.color_inactive
        self.font = font
        self.text = ''
        self.max_length = 20
        self.active = False
        self.label = "图层索引或名称: "
        self.layers = layer_list

    def handle_event(self, event):
        if event.type == pygame.MOUSEBUTTONDOWN:
            if self.rect.collidepoint(event.pos):
                self.active = not self.active
                self.color = self.color_active if self.active else self.color_inactive
                if not self.active:
                    return self.submit()
            else:
                self.active = False
                self.color = self.color_inactive
                return self.submit()
        if event.type == pygame.KEYDOWN and self.active:
            if event.key == pygame.K_RETURN:
                return self.submit()
            elif event.key == pygame.K_ESCAPE:
                self.text = ''
                self.active = False
                self.color = self.color_inactive
            elif event.key == pygame.K_BACKSPACE:
                self.text = self.text[:-1]
            else:
                if len(self.text) < self.max_length and event.unicode.isprintable():
                    self.text += event.unicode
        return None

    def submit(self):
        if not self.text.strip():
            return None
        try:
            idx = int(self.text.strip())
            if 0 <= idx < len(self.layers):
                return self.layers[idx], 'toggle'
            else:
                print(f"索引 {idx} 超出范围 (0-{len(self.layers)-1})")
        except ValueError:
            target = self.text.strip()
            for name in self.layers:
                if name.lower() == target.lower():
                    return name, 'toggle'
            print(f"未找到名为 '{target}' 的图层")
        finally:
            self.text = ''
        return None

    def draw(self, screen):
        label_surf = self.font.render(self.label, True, (255, 255, 255))
        screen.blit(label_surf, (self.rect.x - label_surf.get_width() - 10, self.rect.y + 4))
        pygame.draw.rect(screen, self.color, self.rect, 2)
        txt_surf = self.font.render(self.text, True, (255, 255, 255))
        screen.blit(txt_surf, (self.rect.x + 5, self.rect.y + 5))

# ==================== 地图构建（增强对象渲染） ====================
def build_sprites(tmx_data, camera_group):
    """从 TMX 数据生成所有精灵，返回图层名列表、初始可见性和对象统计"""
    all_layers = list(tmx_data.layers)
    layer_names = [layer.name for layer in all_layers]
    layer_z = {layer.name: idx for idx, layer in enumerate(all_layers)}
    initial_visibility = {layer.name: getattr(layer, 'visible', True) for layer in all_layers}

    # 水动画帧（保持不变）
    water_frames = []
    water_layer_names = [name for name in layer_names if name.lower() == "water"]
    if water_layer_names:
        try:
            water_frames = import_images_from_folder(get_resource_path("terrain/graphics/water"))
            print(f"✅ 加载了 {len(water_frames)} 张水动画帧")
        except FileNotFoundError:
            print("⚠️ 未找到水动画文件夹")

    for layer in all_layers:
        if isinstance(layer, pytmx.TiledTileLayer):
            # 图块层
            if hasattr(layer, 'data') and layer.data is not None:
                is_water = layer.name in water_layer_names and water_frames
                for y, row in enumerate(layer.data):
                    for x, gid in enumerate(row):
                        if gid == 0:
                            continue
                        pos = (x * tmx_data.tilewidth, y * tmx_data.tileheight)
                        if is_water:
                            Water(pos, water_frames, camera_group,
                                  z=layer_z[layer.name], layer_name=layer.name)
                        else:
                            tile_img = tmx_data.get_tile_image_by_gid(gid)
                            if tile_img:
                                Generic(pos, tile_img, camera_group,
                                        z=layer_z[layer.name], layer_name=layer.name)
        elif isinstance(layer, pytmx.TiledObjectGroup):
            for obj in tmx_data.get_layer_by_name(layer.name):
                Generic((obj.x, obj.y), obj.image, camera_group,layer_z[layer.name], layer.name)



    return layer_names, initial_visibility

# ==================== 主程序 ====================
def main():
    pygame.init()
    screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
    pygame.display.set_caption("TMX 地图查看器 (对象调试增强版)")
    clock = pygame.time.Clock()

    try:
        font = pygame.font.Font("汉仪润圆-75W.ttf", 24)
    except:
        font = pygame.font.Font(None, 24)

    # 加载地图
    try:
        tmx_data = load_pygame(get_resource_path("terrain/map.tmx"))
        print("✅ 地图加载成功")
    except Exception as e:
        print(f"❌ 加载地图失败: {e}")
        sys.exit()

    # 构建精灵
    camera_group = CameraGroup()
    layer_names, initial_visibility = build_sprites(tmx_data, camera_group)
    layer_visibility = initial_visibility.copy()

    # 输入框
    input_box = InputBox(150, SCREEN_HEIGHT - 40, 300, 30, font, layer_names)

    # 相机状态
    offset = pygame.math.Vector2()
    dragging = False
    drag_start = (0, 0)
    offset_start = offset.copy()

    # 对象层可见性快捷切换（假设对象层名包含 "object"）
    object_layer_names = [name for name in layer_names if "object" in name.lower()]
    object_visible = all(layer_visibility.get(name, True) for name in object_layer_names)

    running = True
    while running:
        dt = clock.tick(60) / 1000.0

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_l:
                    print("\n=== 图层列表 ===")
                    for i, name in enumerate(layer_names):
                        print(f"  [{i}] {name} (可见: {layer_visibility[name]})")
                    print("================")
                elif event.key == pygame.K_r:
                    layer_visibility.update(initial_visibility)
                    print("✅ 已重置为初始可见性")
                elif event.key == pygame.K_o:   # 新增：切换所有对象层
                    object_visible = not object_visible
                    for name in object_layer_names:
                        layer_visibility[name] = object_visible
                    print(f"🔄 对象层可见性: {'显示' if object_visible else '隐藏'}")

            result = input_box.handle_event(event)
            if result:
                layer_name, action = result
                if action == 'toggle':
                    layer_visibility[layer_name] = not layer_visibility[layer_name]
                    print(f"切换图层 '{layer_name}' 可见: {layer_visibility[layer_name]}")

            # 鼠标拖拽
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if not input_box.rect.collidepoint(event.pos):
                    dragging = True
                    drag_start = pygame.mouse.get_pos()
                    offset_start = offset.copy()
            elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                dragging = False
            elif event.type == pygame.MOUSEMOTION and dragging:
                dx = event.pos[0] - drag_start[0]
                dy = event.pos[1] - drag_start[1]
                offset.x = offset_start.x - dx
                offset.y = offset_start.y - dy

        # 键盘移动
        keys = pygame.key.get_pressed()
        move_speed = 5
        if keys[pygame.K_LEFT]:   offset.x -= move_speed
        if keys[pygame.K_RIGHT]:  offset.x += move_speed
        if keys[pygame.K_UP]:     offset.y -= move_speed
        if keys[pygame.K_DOWN]:   offset.y += move_speed
        camera_group.offset = offset

        # 更新（水动画）
        camera_group.update(dt)

        # ---- 绘制 ----
        screen.fill((30, 30, 30))
        camera_group.custom_draw(layer_visibility)

        # 输入框和提示
        input_box.draw(screen)
        hint = font.render("方向键/鼠标拖拽 | L:列表 | R:重置可见 | O:切换对象层 | 输入框切换图层", True, (200, 200, 200))
        screen.blit(hint, (10, SCREEN_HEIGHT - 60))

        # 状态信息（左上角）
        status_y = 10
        status_lines = [
            f"💧 水动画: {'已加载' if any('water' in name.lower() for name in layer_names) else '无'}",
            f"🖱️ 相机偏移: ({int(offset.x)}, {int(offset.y)})"
        ]
        for line in status_lines:
            surf = font.render(line, True, (0, 255, 255))
            screen.blit(surf, (10, status_y))
            status_y += 30

        pygame.display.flip()

    pygame.quit()
    sys.exit()

if __name__ == "__main__":
    main()