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


# BOM 标识及其对应的编码。
# 注意：长 BOM 必须排在短 BOM 之前，否则 UTF-32-LE 的 BOM (FF FE 00 00) 会
# 被 UTF-16-LE 的 BOM (FF FE) 先命中而误判为 UTF-16-LE。
_BOM_MAP = [
    (codecs.BOM_UTF32_LE, 'utf-32-le'),   # FF FE 00 00
    (codecs.BOM_UTF32_BE, 'utf-32-be'),   # 00 00 FE FF
    (codecs.BOM_UTF8, 'utf-8-sig'),       # EF BB BF
    (codecs.BOM_UTF16_LE, 'utf-16-le'),   # FF FE
    (codecs.BOM_UTF16_BE, 'utf-16-be'),   # FE FF
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


# 单字节遗留编码（windows-1250、iso-8859-5、cp1252、mac_roman 等）把每个高位字节
# 独立映射成字符，能解码任意字节流——_try_decode 对它们永远成功、毫无区分力。
# 用 0x80 探测：所有严格多字节编码（gbk/gb18030/utf-8/shift_jis/big5/euc-*）都
# 不接受 0x80 作为合法字节，解不开；单字节遗留编码则能解开。
_PERMISSIVE_PROBE = b'\x80'


def _is_permissive_single_byte(encoding: str) -> bool:
    """该编码是否为“能解码任意字节流”的单字节遗留编码（decode 成功无意义）。"""
    return _try_decode(_PERMISSIVE_PROBE, encoding)


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
            detected_encoding = result['encoding']
            if detected_encoding is None:
                raise ValueError("encoding is None after check")
            encoding = _normalize_encoding(detected_encoding)

            # charset-normalizer 对“大段 ASCII 夹少量中文”的源码常误报为 windows-1250 /
            # iso-8859-X 这类单字节遗留编码（它们能解码任意字节流，置信度虚高）。此时先做
            # GBK 严格校验：GBK 解得开说明高位字节确实成对组成中文，应优先于单字节猜测。
            if _is_permissive_single_byte(encoding):
                if _try_decode(data, 'gbk'):
                    return EncodingResult(encoding='gbk', confidence=0.85)
                if _try_decode(data, 'gb18030'):
                    return EncodingResult(encoding='gb18030', confidence=0.8)

            if _try_decode(data, encoding):
                confidence = result.get('confidence', 0.9) or 0.9
                return EncodingResult(encoding=encoding, confidence=confidence)
    except Exception:
        pass

    # 尝试 GB18030 解码验证（gb18030 是 gbk 的严格超集：能正确覆盖所有 gbk 文件，
    # 且不会把含 4 字节序列的 gb18030 文件误判为 gbk）
    if _try_decode(data, 'gb18030'):
        return EncodingResult(encoding='gb18030', confidence=0.8)

    # 默认使用 GB18030（超集，比 gbk 更安全）
    return EncodingResult(encoding='gb18030', confidence=0.5)


def detect_file_encoding(file_path: str | Path) -> EncodingResult:
    """
    检测文件编码（仅读取前 32KB 用于检测）
    """
    return detect_file_encoding_details(file_path)[0]


def detect_file_encoding_details(file_path: str | Path) -> tuple[EncodingResult, str]:
    """
    一次读取前 32KB，同时返回编码检测结果与行尾风格。

    返回 (EncodingResult, line_ending)，line_ending 见 detect_line_ending。
    供只探测不读内容的工具使用，避免重复读文件。
    """
    path = Path(file_path)
    with open(path, 'rb') as f:
        data = f.read(32768)

    if not data:
        return EncodingResult(encoding='utf-8', confidence=1.0), 'none'

    return _detect_with_safety_net(data), detect_line_ending(data)


def _detect_with_safety_net(data: bytes) -> EncodingResult:
    """detect_encoding + UTF-8 安全网：误判 UTF-8 但解不开时回退 GB18030（超集，覆盖 gbk）。"""
    result = detect_encoding(data)

    # 安全网：如果检测为 UTF-8 但实际解码失败，尝试 GB18030（超集，覆盖 gbk）
    if result.encoding in ('utf-8', 'utf-8-sig'):
        try:
            data.decode('utf-8')
        except UnicodeDecodeError:
            if _try_decode(data, 'gb18030'):
                return EncodingResult(encoding='gb18030', confidence=0.9)

    return result


def detect_line_ending(data: bytes | str) -> str:
    """
    检测字节流或字符串的行尾风格，仅做统计，不做任何规范化。

    返回 'CRLF' / 'LF' / 'CR' / 'mixed' / 'none'：
    - CRLF：行尾全部为 \\r\\n
    - LF：行尾全部为独立 \\n
    - CR：行尾全部为独立 \\r
    - mixed：同时存在不止一种行尾
    - none：无任何换行符

    接受 str 是为了避免调用方为统计行尾而把整段内容编码成 bytes（O(N) 拷贝）。
    """
    if isinstance(data, str):
        crlf = data.count('\r\n')
        total_lf = data.count('\n')
        total_cr = data.count('\r')
    else:
        crlf = data.count(b'\r\n')
        total_lf = data.count(b'\n')
        total_cr = data.count(b'\r')
    lone_lf = total_lf - crlf
    lone_cr = total_cr - crlf

    if crlf == 0 and lone_lf == 0 and lone_cr == 0:
        return 'none'
    if lone_lf == 0 and lone_cr == 0:
        return 'CRLF'
    if crlf == 0 and lone_cr == 0:
        return 'LF'
    if crlf == 0 and lone_lf == 0:
        return 'CR'
    return 'mixed'
