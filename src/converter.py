"""
编码转换模块
"""

from pathlib import Path


def decode_to_utf8(data: bytes, encoding: str) -> tuple[str, list[str]]:
    """
    将字节数据从指定编码转换为 UTF-8 字符串
    返回 (内容, 警告列表)
    """
    warnings = []

    # 处理带 BOM 的 UTF-8
    if encoding.lower() in ('utf-8-sig', 'utf-8'):
        raw = data[3:] if data.startswith(b'\xef\xbb\xbf') else data
        try:
            return raw.decode('utf-8'), warnings
        except UnicodeDecodeError:
            warnings.append("部分字符无法用 UTF-8 解码，已替换为占位符")
            return raw.decode('utf-8', errors='replace'), warnings

    # 使用指定编码解码
    try:
        return data.decode(encoding), warnings
    except UnicodeDecodeError:
        warnings.append(f"部分字符无法用 {encoding} 解码，已替换为占位符")
        return data.decode(encoding, errors='replace'), warnings


def encode_from_utf8(content: str, encoding: str) -> tuple[bytes, list[str]]:
    """
    将 UTF-8 字符串转换为指定编码的字节数据
    返回 (字节数据, 警告列表)
    """
    warnings = []

    # UTF-8 with BOM - 写回时恢复 BOM
    if encoding.lower() == 'utf-8-sig':
        return b'\xef\xbb\xbf' + content.encode('utf-8'), warnings

    if encoding.lower() == 'utf-8':
        return content.encode('utf-8'), warnings

    try:
        return content.encode(encoding), warnings
    except UnicodeEncodeError:
        warnings.append(f"部分字符无法用 {encoding} 编码，已替换为占位符")
        return content.encode(encoding, errors='replace'), warnings


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
