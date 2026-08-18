"""labels 解析工具 —— Click 命令树与 serve 命令树共用。

从 method spec 的 ``labels`` 字段解析标签列表：list 原样返回、标量包装
为单元素 list、缺失返回空 list。此前该逻辑分别在
:mod:`cliyard.engine.builder` 与 :mod:`cliyard.server.schema_bridge` 各
实现一份（完全等价），现收敛到本模块避免重复。
"""

from __future__ import annotations

from typing import Any


def resolve_labels(method_spec: dict[str, Any]) -> list[str]:
    """从 method spec 的 ``labels`` 字段解析标签列表。

    * ``list`` → 原样返回；
    * 标量（str）→ 包装为单元素 list；
    * 缺失 → 空 list。
    """
    labels = method_spec.get("labels")
    if labels is not None:
        return labels if isinstance(labels, list) else [str(labels)]
    return []
