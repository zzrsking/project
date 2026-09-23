1.3.9 —— 继续开发 Structure：修好 Hall 的连接问题。

本阶段目标：实现对 Structure 的【配置型创建】支持。

背景（1.3.8 已完成的部分）
    1.3.8 给 resource manager 补上了资源掌控能力、细分了很多属性，并让 serializer
    适配它 —— 存档不再需要保存图片的原始数据（见 SERIALIZER_IMAGE_FORMAT.md）。
    Structure 这一侧也已经有了：端口（port）连接范式 v3、Layer v2、配方式存读档
    （structure_persist.py）。

本阶段要做的两件事
    一、修 Hall 的连接问题
        Hall 目前是"手工装配 + 事后补丁"：build() 里手工算墙的摆放坐标、靠端口吸附
        对齐，再叠一层 junction 纹理修正（sync_junctions / mark_junction /
        note_opening_attached / head_run 那几个开关），几何与连接状态互相耦合。
        结果是：连上没连上、连的是哪一端、墙头该不该画收口纹，这几件事散落在
        Hall、VerticalWall、wall_joint、opening 四处，改一处很容易在画面上出问题
        而不报错。本阶段把 Hall 的连接统一到"端口簇 -> 自决策"这一条路径上，
        去掉手工摆位与事后补丁，让连接的几何与贴图都由拓扑唯一决定。

    二、实现 Structure 的配置型创建支持
        现状：每种结构各自写一个 build()，参数散在签名里，装配步骤写死在函数体内；
        新建一种结构要复制一整段装配代码。
        目标：用一份【声明式配置】描述一个结构 —— 它由哪些部件、按什么规则摆放、
        边界上哪些格是端口、连上之后贴图怎么变 —— 由统一的构建器读配置装配出来。
        这样新增结构 = 写一份配置，而不是写一段装配代码；同时复用到存档配方
        （structure_persist 的 from_recipe 本质上就是"配方 -> 结构"，两者应当统一
        成同一套描述）。

约束（沿用，不要破坏）
    · 定义新 Structure 必须满足的条件见 STRUCTURE_REQUIREMENTS.md 的检查清单；
      端口联动 / 渲染顺序 / 淡出信号这三块要么满足、要么在画面上出问题而不报错。
    · 结构不进对象图，只存"创建配方 + 用 instance_id/端口语义名描述的连接表"
      （见 STRUCTURE_PERSISTENCE.md）。所以配置型创建必须保证：
      同样的参数 + 同样的仓库规则 ⇒ 同样的几何，否则读档会画歪。

本版已清理
    移除了 1.3.8 遗留的调试产物：tests/ 目录、根目录 _*.png / _hall_dump.txt 调试图与
    转储、game_debug.log / serializer.log、save_test.json，以及 src 下的 test.json、
    test_2.json、ui/elements/test*.py 独立演示脚本、script/ 下的一次性脚本。
    另外删掉了两个死文件：core/build_game/deepseek_python_20260902_25b38c.py（乱码导出）
    与 utils/serializer.py（setup.py 的乱码重复件，全项目无引用，真正的序列化器是
    src/serializer.py）。

注意
    文档里的自检命令（python tests/xxx.py）在本版已失效 —— 测试目录已移除，
    需要时按上述清单重建。存档默认文件名 save_test.json（src/core/scene_io.py）
    首次存档会自动重建。
