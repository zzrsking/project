import pygame

from src.ui.elements.fonts import get_font


class InventoryInterface:
    def __init__(self, screen_rect=pygame.Rect(0,0,0,0)):
        self.panel = pygame.Rect(200, 100, 600, 400)
        self.show = False
        self.slot_size = 64
        self.grid_cols = 8
        self.grid_rows = 5
        self.slots = [{"item": None, "rect": pygame.Rect(200 + (i%self.grid_cols)*self.slot_size, 100 + (i//self.grid_cols)*self.slot_size, self.slot_size, self.slot_size)} for i in range(self.grid_cols*self.grid_rows)]
        self.dragged_item = None
        self.visible = False

    def toggle(self):
        self.visible = not self.visible

    def add_item(self, item):
        if item.stackable:
            for slot in self.slots:
                if slot["item"] and slot["item"].id == item.id:
                    slot["item"].quantity += item.quantity
                    return
        for slot in self.slots:
            if not slot["item"]:
                slot["item"] = item
                return
        print("背包已满！")

    def draw(self, surface):
        if not self.visible: return
        pygame.draw.rect(surface, (40,40,50,220), self.panel)
        for slot in self.slots:
            pygame.draw.rect(surface, (60,60,80), slot["rect"], 2)
            if slot["item"]:
                slot["item"].draw_icon(surface, slot["rect"].topleft)
                if slot["item"].quantity > 1:
                    font = get_font(18, None)
                    text = font.render(str(slot["item"].quantity), True, (255,255,255))
                    surface.blit(text, (slot["rect"].right - 25, slot["rect"].bottom - 20))
        if self.dragged_item:
            mouse_pos = pygame.mouse.get_pos()
            self.dragged_item.draw_icon(surface, (mouse_pos[0] - 16, mouse_pos[1] - 16))

    def handle_events(self, event):
        if not self.visible: return
        if event.type == pygame.MOUSEBUTTONDOWN:
            for slot in self.slots:
                if slot["rect"].collidepoint(event.pos):
                    if slot["item"]:
                        self.dragged_item = slot["item"]
                        slot["item"] = None
        elif event.type == pygame.MOUSEBUTTONUP:
            if self.dragged_item:
                for slot in self.slots:
                    if slot["rect"].collidepoint(event.pos):
                        if slot["item"]:
                            temp = slot["item"]
                            slot["item"] = self.dragged_item
                            self.dragged_item = temp
                        else:
                            slot["item"] = self.dragged_item
                            self.dragged_item = None

    def get(self, item_id, default=0):
        total = 0
        for slot in self.slots:
            if slot["item"] and slot["item"].id == item_id:
                total += slot["item"].quantity
        return total if total > 0 else default

