"""
编码状态存储 - 内存中存储文件路径到编码的映射
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_MAX_STORE_SIZE = 1000


@dataclass(frozen=True)
class _Record:
    """单条编码记录:编码 + 新鲜度快照(mtime/size)+ 置信度。

    mtime/size 用于 read 跳过检测:文件未改动时复用已检测的编码,避免重复探测
    (2MB GBK 文件检测 ~138ms)。当记录对应文件无实体(如 store 时路径尚无文件)时
    记为哨兵,get_fresh_encoding 永远判为不新鲜,迫使重新检测。
    """
    encoding: str
    mtime: float
    size: int
    confidence: float


# 不带新鲜度快照的哨兵:size=-1 表示无可用快照,新鲜度永远判定为不新鲜
_NO_SNAPSHOT: tuple[float, int] = (-1.0, -1)

# 使用字典存储文件路径 -> 记录的映射
_encoding_store: dict[str, _Record] = {}


def normalize_path(file_path: str) -> str:
    """标准化路径"""
    return str(Path(file_path).resolve())


def _key(file_path: str, normalized: bool) -> str:
    """缓存键。normalized=True 时调用方保证 file_path 已是 resolve 后的规范化路径,
    直接用作键——省去 Windows 上每次 normalize_path 再 resolve 打开文件句柄的开销
    (Defender 可能拦截扫描每一次)。默认仍防御性 resolve,对外行为不变。"""
    return file_path if normalized else normalize_path(file_path)


def _snapshot(file_path: str) -> tuple[float, int]:
    """取文件 mtime/size 快照;文件不存在或不可 stat 时返回哨兵。"""
    try:
        st = Path(file_path).stat()
        return st.st_mtime, st.st_size
    except OSError:
        return _NO_SNAPSHOT


def store_encoding(file_path: str, encoding: str, confidence: float = 1.0, *,
                   normalized: bool = False) -> None:
    """存储文件的编码信息(并捕获新鲜度快照)。

    normalized=True:调用方保证 file_path 已是 resolve 后的规范化路径,直接用作键。
    """
    key = _key(file_path, normalized)
    mtime, size = _snapshot(file_path)
    _encoding_store[key] = _Record(encoding, mtime, size, confidence)
    # 超出上限时淘汰最早的记录（dict 按插入顺序保留）
    while len(_encoding_store) > _MAX_STORE_SIZE:
        _encoding_store.pop(next(iter(_encoding_store)))


def get_encoding(file_path: str, *, normalized: bool = False) -> Optional[str]:
    """获取文件的编码信息"""
    record = _encoding_store.get(_key(file_path, normalized))
    return record.encoding if record is not None else None


def get_fresh_encoding(file_path: str, *, normalized: bool = False) -> Optional[tuple[str, float]]:
    """若该文件有缓存记录且自记录以来未改动,返回 (编码, 置信度);否则返回 None。

    用于 read 跳过重复检测:以 mtime+size 判定文件是否变化。无快照(哨兵)、
    文件已不存在或已改动时返回 None,调用方应重新检测。
    """
    record = _encoding_store.get(_key(file_path, normalized))
    if record is None or record.size == -1:
        return None
    mtime, size = _snapshot(file_path)
    if size == -1:
        return None  # 文件已不存在
    if mtime == record.mtime and size == record.size:
        return record.encoding, record.confidence
    return None


def has_encoding(file_path: str, *, normalized: bool = False) -> bool:
    """检查是否有文件的编码记录"""
    return _key(file_path, normalized) in _encoding_store


def clear_encoding(file_path: str) -> None:
    """清除指定文件的编码记录"""
    normalized = normalize_path(file_path)
    _encoding_store.pop(normalized, None)


def clear_all() -> None:
    """清除所有编码记录"""
    _encoding_store.clear()


def get_all_encodings() -> dict[str, str]:
    """获取所有存储的编码记录"""
    return {path: record.encoding for path, record in _encoding_store.items()}
