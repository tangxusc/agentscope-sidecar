"""底座异常层次(设计文档 §11.10)。

**只保留真正会被抛出的类型。** 定义但从不抛出的异常比没有更糟 —— 调用方会
以为可以捕获它。
"""

from __future__ import annotations


class SidecarError(Exception):
    """底座异常基类。"""


class ConventionError(SidecarError):
    """约定推断失败:模型引用非法或缺少凭据、选项非法、任务为空。"""


class PluginImportError(SidecarError):
    """模块导入失败(语法错误、import 错误)。"""


class DeclarationError(SidecarError):
    """注册非法:路由/命令/服务重名、HTTP 方法或路径非法、``setup`` 签名非法。"""


class ServiceNotFound(SidecarError):
    """``ServiceRef`` 解析失败 —— 惰性代理被使用时服务仍不存在。"""

    def __init__(self, name: str, available: list[str] | None = None) -> None:
        detail = f"已注册:{sorted(available)}" if available else ""
        super().__init__(f"服务 {name!r} 不存在。{detail}")
        self.name = name


__all__ = [
    "ConventionError",
    "DeclarationError",
    "PluginImportError",
    "ServiceNotFound",
    "SidecarError",
]
