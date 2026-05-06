"""
编码检测模块 - 使用 charset-normalizer 检测文件编码
"""

import codecs
from pathlib import Path
from typing import NamedTuple

import charset_normalizer


class EncodingResult(NamedTuple):
    """编码检测结果"""
    encoding: str
    confidence: float


# BOM 标识及其对应的编码
_BOM_MAP = [
    (codecs.BOM_UTF8, 'utf-8-sig'),
    (codecs.BOM_UTF16_LE, 'utf-16-le'),
    (codecs.BOM_UTF16_BE, 'utf-16-be'),
    (codecs.BOM_UTF32_LE, 'utf-32-le'),
    (codecs.BOM_UTF32_BE, 'utf-32-be'),
]

# 用于快速检测高位字节
_DEL_ASCII = bytes(range(128))


def _detect_by_bom(data: bytes) -> str | None:
    """通过 BOM 检测编码"""
    for bom, encoding in _BOM_MAP:
        if data.startswith(bom):
            return encoding
    return None


def _normalize_encoding(detected: str) -> str:
    """
    标准化编码名称
    将检测到的编码名称转换为 Python codecs 支持的名称
    """
    lower = detected.lower().replace('-', '').replace('_', '')

    # GB2312 是 GBK 的子集，统一使用 GBK
    if lower in ('gb2312', 'gbk'):
        return 'gbk'

    # GB18030 是 GBK 的超集，保留以避免数据丢失
    if lower == 'gb18030':
        return 'gb18030'

    # UTF-8（不含 BOM）
    if lower == 'utf8':
        return 'utf-8'

    # UTF-8 with BOM，保留标识以便写回时恢复 BOM
    if lower == 'utf8sig':
        return 'utf-8-sig'

    # ASCII 兼容 UTF-8
    if lower == 'ascii':
        return 'utf-8'

    # 其他编码保持原样
    return detected.lower()


def _try_decode(data: bytes, encoding: str) -> bool:
    """尝试用指定编码解码数据，成功返回 True"""
    try:
        data.decode(encoding)
        return True
    except (UnicodeDecodeError, LookupError):
        return False


def detect_encoding(data: bytes) -> EncodingResult:
    """
    检测字节数据的编码
    优先检测中文编码（GBK/GB18030）
    """
    # 先检查 BOM
    bom_encoding = _detect_by_bom(data)
    if bom_encoding:
        return EncodingResult(encoding=bom_encoding, confidence=1.0)

    # 快速检测是否包含高位字节
    has_high_byte = bool(data.translate(None, _DEL_ASCII))

    if not has_high_byte:
        # 纯 ASCII，使用 UTF-8
        return EncodingResult(encoding='utf-8', confidence=1.0)

    # 使用 charset-normalizer 检测
    try:
        result = charset_normalizer.detect(data)
        if result and result.get('encoding'):
            encoding = _normalize_encoding(result['encoding'])
            if _try_decode(data, encoding):
                confidence = result.get('confidence', 0.9) or 0.9
                return EncodingResult(encoding=encoding, confidence=confidence)
    except Exception:
        pass

    # 尝试 GBK 解码验证（覆盖大多数中文文件）
    if _try_decode(data, 'gbk'):
        return EncodingResult(encoding='gbk', confidence=0.8)

    # 默认使用 GBK
    return EncodingResult(encoding='gbk', confidence=0.5)


def detect_file_encoding(file_path: str | Path) -> EncodingResult:
    """
    检测文件编码（仅读取前 32KB 用于检测）
    """
    path = Path(file_path)
    with open(path, 'rb') as f:
        data = f.read(32768)

    if not data:
        return EncodingResult(encoding='utf-8', confidence=1.0)

    result = detect_encoding(data)

    # 安全网：如果检测为 UTF-8 但实际解码失败，尝试 GBK
    if result.encoding in ('utf-8', 'utf-8-sig'):
        try:
            data.decode('utf-8')
        except UnicodeDecodeError:
            if _try_decode(data, 'gbk'):
                return EncodingResult(encoding='gbk', confidence=0.9)

    return result
