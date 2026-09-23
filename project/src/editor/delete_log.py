"""delete_log.py —— 删除操作的可撤销记录（接 undo.py 的统一序号协议）。

问题
----
删除本来**完全撤不回来**：右键删掉的对象/结构当场就没了。
Ctrl+Z 只认结构生成器与横墙拖拽工具，删除这一路根本不在撤销体系里。

做法
----
按 undo.py 的工具协议（暴露 ``last_seq``、实现 ``undo()``）做一个删除日志，
于是它能和另外两个工具一起被 ``EditorManager.undo()`` 按
【真实发生顺序】撤销 —— 谁最近撤谁，不必把各工具的数据结构合并。

为什么记"恢复闭包"而不是只记对象
--------------------------------
删除普通对象与删除组合结构(Structure)走的路径完全不同：
  · 普通对象   world.remove_game_object(obj)（顺带 kill + 注销网格 + 出层）
  · 组合结构   unregister_object_from_grid + layers.remove_object，
              而且删掉的如果是玩家还要把 world.active_player 清掉
把"当时怎么删的"对应着记进闭包，撤销时才不会出现"删得掉、放不回"的错位。
"""

from src.editor.undo import next_seq

MAX_DEPTH = 128          # 栈深上限，防止长时间编辑无限增长


class DeleteLog:
    """删除日志：与 wall_drag_tool / structure_builder 同一套撤销协议。"""

    def __init__(self):
        self.undo_list = []          # [(全局序号, 恢复闭包, 标签), ...]
        self._last_seq = 0

    # ---- undo.py 协议：Ctrl+Z 靠它比较"谁最近" ----
    @property
    def last_seq(self) -> int:
        """栈顶序号；栈空返回 0（协议要求）。"""
        return self._last_seq

    @property
    def depth(self) -> int:
        return len(self.undo_list)

    # ---- 记录 ----
    def record(self, label, restore_fn):
        """登记一条删除：restore_fn() 必须能把对象原样放回原来那一层。"""
        if not callable(restore_fn):
            return 0
        seq = next_seq()
        self.undo_list.append((seq, restore_fn, label))
        if len(self.undo_list) > MAX_DEPTH:
            del self.undo_list[0:len(self.undo_list) - MAX_DEPTH]
        self._last_seq = seq
        return seq

    def clear(self):
        self.undo_list.clear()
        self._last_seq = 0

    # ---- 撤销 ----
    def undo(self) -> bool:
        if not self.undo_list:
            self._last_seq = 0
            return False

        _seq, restore, label = self.undo_list.pop()
        try:
            restore()
            print(f"[undo] 已恢复：{label}")
            return True
        except Exception as e:            # 撤销失败不该把编辑器带崩
            print(f"[undo] 恢复 {label!r} 失败: {type(e).__name__}: {e}")
            return False
        finally:
            self._last_seq = self.undo_list[-1][0] if self.undo_list else 0
