"""
编码转换模块
"""

import codecs
from pathlib import Path


# UTF-16/32 各编码对应的 BOM 字节：读时去除、写时补回，
# 保证 BOM 与字节序都与原文件逐字节一致（显式字节序编解码，不用会改字节序的自动编解码）。
_BOM_BY_ENCODING: dict[str, bytes] = {
    'utf-16-le': codecs.BOM_UTF16_LE,
    'utf-16-be': codecs.BOM_UTF16_BE,
    'utf-32-le': codecs.BOM_UTF32_LE,
    'utf-32-be': codecs.BOM_UTF32_BE,
}


def decode_to_utf8(data: bytes, encoding: str) -> tuple[str, list[str]]:
    """
    将字节数据从指定编码转换为 UTF-8 字符串。
    无法忠实解码时抛 UnicodeDecodeError——绝不静默替换为占位符（不篡改原则）。
    """
    warnings: list[str] = []
    lower = encoding.lower()

    # 处理带 BOM 的 UTF-8
    if lower in ('utf-8-sig', 'utf-8'):
        raw = data[3:] if data.startswith(b'\xef\xbb\xbf') else data
        return raw.decode('utf-8'), warnings

    # UTF-16/32：显式字节序编解码，读时去除检测到的 BOM（否则内容会多出前导 U+FEFF）
    if lower in _BOM_BY_ENCODING:
        bom = _BOM_BY_ENCODING[lower]
        raw = data[len(bom):] if data.startswith(bom) else data
        return raw.decode(lower), warnings

    # 使用指定编码解码
    return data.decode(encoding), warnings


def encode_from_utf8(content: str, encoding: str) -> tuple[bytes, list[str]]:
    """
    将 UTF-8 字符串转换为指定编码的字节数据。
    无法忠实编码时抛 UnicodeEncodeError——绝不静默替换为 '?'（不篡改原则）。
    """
    warnings: list[str] = []
    lower = encoding.lower()

    # UTF-8 with BOM - 写回时恢复 BOM
    if lower == 'utf-8-sig':
        return b'\xef\xbb\xbf' + content.encode('utf-8'), warnings

    # UTF-16/32：写回时补回对应的 BOM，字节序与原文件一致
    if lower in _BOM_BY_ENCODING:
        return _BOM_BY_ENCODING[lower] + content.encode(lower), warnings

    if lower == 'utf-8':
        return content.encode('utf-8'), warnings

    return content.encode(encoding), warnings


def read_file_as_utf8(file_path: str | Path, encoding: str) -> tuple[str, list[str]]:
    """
    读取文件并转换为 UTF-8
    返回 (内容, 警告列表)
    """
    path = Path(file_path)
    with open(path, 'rb') as f:
        data = f.read()
    return decode_to_utf8(data, encoding)


def write_file_from_utf8(file_path: str | Path, content: str, encoding: str) -> list[str]:
    """
    将 UTF-8 内容写入文件（转换为目标编码）
    返回警告列表
    """
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    data, warnings = encode_from_utf8(content, encoding)
    with open(path, 'wb') as f:
        f.write(data)
    return warnings


def is_encoding_supported(encoding: str) -> bool:
    """检查编码是否被 Python 支持"""
    try:
        ''.encode(encoding)
        return True
    except LookupError:
        return False
