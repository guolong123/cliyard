"""敏感信息脱敏工具 —— 供 serve 事件回调广播时使用。

递归遍历任意嵌套对象，将键名（大小写不敏感、子串匹配）命中
``token`` / ``password`` / ``authorization`` / ``secret`` /
``api_key`` / ``access_key`` / ``credential`` / ``passphrase`` /
``pwd`` / ``jwt`` / ``bearer`` 的值替换为 ``***``，避免敏感原始值
通过事件流出到前端或日志。

Example::

    >>> redact_sensitive({"query": {"token": "abc"}, "name": "ok"})
    {'query': {'token': '***'}, 'name': 'ok'}
"""

from __future__ import annotations

from typing import Any

_SENSITIVE_KEYWORDS = (
    "token",
    "password",
    "authorization",
    "secret",
    "apikey",
    "accesskey",
    "credential",
    "passphrase",
    "pwd",
    "jwt",
    "bearer",
)


def is_sensitive_key(key: str) -> bool:
    """Return ``True`` if *key* matches a sensitive-keyword pattern.

    大小写不敏感、子串匹配，且忽略 ``-`` / ``_`` 符号：``token``、
    ``Token``、``apiToken``、``Authorization``、``X-Api-Key``、
    ``access_key`` 等均命中。
    """
    normalized = str(key).lower().replace("-", "").replace("_", "")
    return any(kw in normalized for kw in _SENSITIVE_KEYWORDS)


def redact_sensitive(obj: Any) -> Any:
    """Recursively replace sensitive values with ``***``.

    * dict 中键名命中的键值替换为 ``***``；
    * 嵌套 dict / list 递归处理；
    * 其余值（str / int / float / None / 自定义对象）原样透传。
    """
    if isinstance(obj, dict):
        return {
            key: ("***" if is_sensitive_key(key) else redact_sensitive(value))
            for key, value in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [redact_sensitive(item) for item in obj]
    return obj
