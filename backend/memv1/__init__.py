"""backend.memv1 —— M1 记忆工程包（v4.1.1）。

模块规划：
- ``schema``      ：MemEntry 五要素 schema（M1-A）
- ``datatrack``   ：数据轨三层（M1-A）
- ``mishearing``  ：听错入口分级（M1-D，本子项目）

注意：``schema.py`` / ``datatrack.py`` 可能尚未落地。为避免在本包导入时
强制要求它们存在，__init__ 不自动 import 任何子模块——各子项目按契约自建
最小桩、独立引入，互不硬依赖。
"""
