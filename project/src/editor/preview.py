import pygame

def draw_placement_preview(surface, camera, preview_obj, alpha=128):
    """在屏幕上绘制半透明的放置预览对象"""
    if preview_obj is None:
        return

    zoom = camera.zoom
    screen_center = camera.world_to_screen(preview_obj.world_position.center)
    orig_w = preview_obj.world_position.width
    orig_h = preview_obj.world_position.height
    scaled_w = max(1, int(orig_w * zoom))
    scaled_h = max(1, int(orig_h * zoom))
    scaled_image = pygame.transform.scale(preview_obj.image, (scaled_w, scaled_h))
    scaled_image.set_alpha(alpha)

    draw_pos = (screen_center[0] - scaled_w / 2,
                screen_center[1] - scaled_h / 2)
    surface.blit(scaled_image, draw_pos)