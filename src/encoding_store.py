"""
编码状态存储 - 内存中存储文件路径到编码的映射
"""

from pathlib import Path
from typing import Optional

_MAX_STORE_SIZE = 1000

# 使用字典存储文件路径 -> 编码的映射
_encoding_store: dict[str, str] = {}


def normalize_path(file_path: str) -> str:
    """标准化路径"""
    return str(Path(file_path).resolve())


def store_encoding(file_path: str, encoding: str) -> None:
    """存储文件的编码信息"""
    normalized = normalize_path(file_path)
    _encoding_store[normalized] = encoding
    # 超出上限时淘汰最早的记录（dict 按插入顺序保留）
    while len(_encoding_store) > _MAX_STORE_SIZE:
        _encoding_store.pop(next(iter(_encoding_store)))


def get_encoding(file_path: str) -> Optional[str]:
    """获取文件的编码信息"""
    normalized = normalize_path(file_path)
    return _encoding_store.get(normalized)


def has_encoding(file_path: str) -> bool:
    """检查是否有文件的编码记录"""
    normalized = normalize_path(file_path)
    return normalized in _encoding_store


def clear_encoding(file_path: str) -> None:
    """清除指定文件的编码记录"""
    normalized = normalize_path(file_path)
    _encoding_store.pop(normalized, None)


def clear_all() -> None:
    """清除所有编码记录"""
    _encoding_store.clear()


def get_all_encodings() -> dict[str, str]:
    """获取所有存储的编码记录"""
    return dict(_encoding_store)
