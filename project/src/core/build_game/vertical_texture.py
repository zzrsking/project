"""vertical_texture.py —— 竖墙纹理集中判定表（用户定稿）

列约定：按 (墙在左? , 纹偏右?) 四格 → 列索引
  列0 = 左墙右纹  列1 = 右墙左纹  列2 = 左墙左纹  列3 = 右墙右纹
  tex_right=True 且 左墙 → 列0；tex_right=True 且 右墙 → 列3
  tex_right=False 且 左墙 → 列2；tex_right=False 且 右墙 → 列1
值：int = 直接该 House 编号；("flip", base) = 把 base 水平翻转；None = 该段不用此纹。

半格规则：列0(左墙右纹)与列1(右墙左纹)是“默认贴合不跨半格”；
        列2(左墙左纹)与列3(右墙右纹)是“需贴半格”的纹。

段：(顶部) head/head_unconn/top_mid/top_tail/tail_unconn
    (侧面) side_mid/side_bottom
顶部对应 length 段，侧面对应 height 段。
"""

_TABLE = {
    "head":        [8, 10, 22, ("flip", 22)],        # 顶部头部
    "head_unconn": [("flip", 33), 33, None, None],   # 顶部头部未连接
    "top_mid":     [16, 18, ("flip", 23), 23],       # 顶部中段
    # 顶部中段·跨半格：墙上"多出半格"的那半格（补厚度）。
    # !! 占位 !! 列2(左墙左纹)=31 / 列3(右墙右纹)=23 —— 具体 ID 与翻转由美术侧定，
    # 这里先把逻辑接通：头格跨半格(22)时，中段也要一起补，否则只有头格显厚度。
    "top_mid_cross": [16, 18, 31, ("flip", 23)],
    # 顶部尾段。!! 待确认 !! 本行的注释与列值对不上：
    #   表里 col2(左墙左纹)=15、col3(右墙右纹)=7；
    #   而原来的注释写"跨半格: 左墙7/右墙15"。
    #   总有一边写反了。但 7 与 15 本身就互为水平镜像，所以镜像自检【抓不到】
    #   这种对调（见 tests/test_texture_table.py 的"已知盲区"，已登记在
    #   AMBIGUOUS_ROWS 里，新增同类行会让测试失败）。
    #   在确认之前【行为保持原样】，只把冲突记在这里，免得被忽略。
    "top_tail":    [24, 26, 15, 7],
    "tail_unconn": [45, ("flip", 45), None, None],   # 顶部尾端未连接
    "side_mid":    [44, ("flip", 44), ("flip", 49), 49],   # 侧面中部
    "side_mid_nocross":[55, ("flip", 55), ("flip", 47), 47],
    "side_bottom": [43, ("flip", 43), ("flip", 50), 50],   # 侧面底部
    "side_bottom_nocross":[63, ("flip", 63), ("flip", 39), 39],
}


def _col(wall_left: bool, tex_right: bool) -> int:
    if tex_right:
        return 0 if wall_left else 3
    return 2 if wall_left else 1


def vertical_segment(segment: str, *, wall_left: bool = True, tex_right: bool = True) -> tuple:
    """按表返回该段应显示的 (House编号, 是否翻转)。"""
    row = _TABLE.get(segment)
    if row is None:
        return ("", False)
    col = _col(wall_left, tex_right)
    if col >= len(row) or row[col] is None:      # 未连接段不提供该纹 → 退回默认贴合列
        col = 0 if wall_left else 1
    val = row[col]
    if isinstance(val, tuple):                    # ("flip", base)
        return (str(val[1]), True)
    return (str(val), False)


def vertical_cell_frame(cell_type: str, *, orientation: str = "left",
                        head_right: bool = False, head_left: bool = False,
                        tail_connected: bool = False, tail_right: bool = False,
                        tail_cross: bool = False, head_cross: bool = False,
                        head_run: bool = False, cross_run: bool = False):
    """根据段类型和连接状态返回 (House编号, 是否翻转)

    head_run ：本墙的【头格】上面还有同一堵墙继续延伸（走廊串联 / 走廊接房子），
               此时头格不该画"自由端头"（head_unconn = 33 那种收口纹），
               而应该跟 top_run 一样用贯通纹（16/18）。
    cross_run：本格在【墙顶补厚度那一段】里 —— 头格下面 height-1 格。
               头格自己用 head 那一档的跨半格纹（22）；紧接其下的 height-1 格用
               top_mid_cross（占位 31/23）。整段一共 height 格（头 1 + 其余 height-1），
               再往下就回到普通贯通纹 —— 只补头格看不出厚度，补整根又会糊。
    """
    wall_left = (orientation == "right")
    tex_right = wall_left  # 默认贴合纹（不跨半格）

    # 根据 cell_type 决定是否使用跨半格纹理
    if cell_type in ("top", "top_run", "top_tail"):        # 头部相关段
        # head_run 优先：头格已经"接上去了"，跨半格收口逻辑不再适用
        if cell_type == "top" and head_cross and not head_run:
            tex_right = not wall_left
        # 中段：只有"墙顶那一段"里的格才补那半格（见 cross_run）
        if cell_type == "top_run" and cross_run:
            tex_right = not wall_left
        # top_tail 使用 tail_cross（头部末行）
        if cell_type == "top_tail" and tail_cross:
            tex_right = not wall_left
    elif cell_type in ("middle", "bottom"):               # 侧面段使用 tail_cross
        if tail_cross:
            tex_right = not wall_left

    # 映射到 segment 名称（兼容旧段名）
    seg_map = {
        # 头格：接上去了（head_run）就与中段同纹；否则按"头是否被连"选 8/10 或自由端 33
        "top": "top_mid" if head_run else ("head" if (head_right or head_left) else "head_unconn"),
        # 中段：墙顶补厚度那一段用"补厚度"那一行
        "top_run": "top_mid_cross" if cross_run else "top_mid",
        "top_tail": "top_tail" if tail_connected else "tail_unconn",
        "middle": "side_mid" if tail_cross else "side_mid_nocross",
        "bottom": "side_bottom"if tail_cross else "side_bottom_nocross",
    }
    seg = seg_map.get(cell_type, "top_mid")  # 默认
    return vertical_segment(seg, wall_left=wall_left, tex_right=tex_right)