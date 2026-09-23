import pygame
import sys
import os
import shutil
from pathlib import Path

# 项目根目录（测试脚本所在目录）
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

# 导入 resource_manager
from src.core.resource_manager import (
    load_image,
    load_tileset,
    load_procedural_surface,
    get_id_from_surface,
    get_surface_by_id,
    get_frame_from_tileset,
    register_existing_surface,
    Tileset,
    ResourceMatcher,
)

ASSETS_DIR = PROJECT_ROOT / "assets"

def create_test_image_file():
    """在 project/assets/ 下创建测试精灵表图片（4帧）"""
    ASSETS_DIR.mkdir(exist_ok=True)
    path = ASSETS_DIR / "test_spritesheet.png"

    surf = pygame.Surface((256, 64), pygame.SRCALPHA)
    colors = [(255,0,0), (0,255,0), (0,0,255), (255,255,0)]
    for i, color in enumerate(colors):
        rect = pygame.Rect(i*64, 0, 64, 64)
        surf.fill(color, rect)
        font = pygame.font.SysFont(None, 48)
        text = font.render(str(i), True, (255,255,255))
        surf.blit(text, (i*64+20, 10))

    pygame.image.save(surf, str(path))
    print(f"测试图片已创建: {path}")

def test_all():
    pygame.init()
    print("Pygame 初始化完成")

    # 1. 创建测试图片（在 assets/ 下）
    create_test_image_file()
    # 使用相对路径（不带 assets/），因为 get_resource_path 会自动在 assets 下查找
    rel_path = "test_spritesheet.png"

    # 2. load_image 基本加载
    print("\n--- 测试 load_image ---")
    frames = load_image(rel_path, frame_count=4, scale=(32, 32))
    print(f"加载帧数: {len(frames)}")
    print(f"第一帧大小: {frames[0].get_size()}")

    first_frame_id = get_id_from_surface(frames[0])
    print(f"第一帧的 ID: {first_frame_id}")
    main_id = first_frame_id.split('_')[0]
    print(f"主 ID: {main_id}")

    all_frames = get_surface_by_id(main_id)
    print(f"通过主ID获取资源类型: {type(all_frames)}, 长度: {len(all_frames)}")

    frame_2 = get_surface_by_id(f"{main_id}_2")
    print(f"通过帧ID '{main_id}_2' 获取的帧大小: {frame_2.get_size()}")

    # 3. load_tileset 便捷加载
    print("\n--- 测试 load_tileset ---")
    tileset = load_tileset(rel_path, frame_count=4, scale=(32, 32))
    print(f"Tileset ID: {tileset.id}")
    print(f"Tileset 帧数: {len(tileset)}")
    print(f"索引1帧大小: {tileset[1].get_size()}")
    print(f"get_frame(3) 帧大小: {tileset.get_frame(3).get_size()}")

    # 4. 程序化资源
    print("\n--- 测试程序化资源 ---")
    circ = load_procedural_surface('wall_circle', ((0,0), 20))
    circ_id = get_id_from_surface(circ)
    circ2 = load_procedural_surface('wall_circle', ((0,0), 20))
    circ2_id = get_id_from_surface(circ2)
    print(f"程序化圆形 ID1: {circ_id}, ID2: {circ2_id}, 相同? {circ_id == circ2_id}")

    # 5. 手动注册外部 Surface
    print("\n--- 测试手动注册 ---")
    manual_surf = pygame.Surface((50, 50))
    manual_surf.fill((128, 128, 255))
    manual_id = register_existing_surface(manual_surf)
    print(f"手动注册ID: {manual_id}")
    retrieved = get_surface_by_id(manual_id)
    print(f"检索到 Surface 大小: {retrieved.get_size()}")

    # 6. ResourceMatcher
    print("\n--- 测试 ResourceMatcher ---")
    matcher = ResourceMatcher()
    exist = matcher.get(main_id)
    print(f"匹配器获取主ID资源类型: {type(exist)}")
    none = matcher.get("nonexistent_id")
    print(f"匹配器获取不存在的ID: {none}")

    def missing_callback(id_str):
        print(f"ID '{id_str}' 缺失，返回默认表面")
        default = pygame.Surface((10,10))
        default.fill((255,0,255))
        return default

    matcher_with_cb = ResourceMatcher(missing_handler=missing_callback)
    default_surf = matcher_with_cb.get("missing_id")
    print(f"回调返回的 Surface 大小: {default_surf.get_size()}")

    frame_from_matcher = matcher_with_cb.get_frame(main_id, 1)
    print(f"通过匹配器获取帧 (主ID,1): {frame_from_matcher is not None}")

    # 7. 内容去重测试
    print("\n--- 测试内容去重 ---")
    copy_path = ASSETS_DIR / "test_copy.png"
    shutil.copy(ASSETS_DIR / "test_spritesheet.png", copy_path)
    frames_copy = load_image("test_copy.png", frame_count=4, scale=(32, 32))
    copy_id = get_id_from_surface(frames_copy[0]).split('_')[0]
    print(f"副本主ID: {copy_id}, 与原始主ID相同? {copy_id == main_id}")
    os.remove(copy_path)

    # 8. 帧ID验证
    print("\n--- 帧ID验证 ---")
    for i in range(len(frames)):
        fid = get_id_from_surface(frames[i])
        expected = f"{main_id}_{i}"
        print(f"帧 {i} 的ID: {fid}, 期望: {expected}, 匹配: {fid == expected}")

    # 9. 清理
    os.remove(ASSETS_DIR / "test_spritesheet.png")
    print("\n测试完成，清理测试文件。")
    pygame.quit()

if __name__ == "__main__":
    test_all()