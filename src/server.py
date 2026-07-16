#!/usr/bin/env python3
"""
MCP 文件编码自动转换服务器

功能：
1. 读取文件时自动检测编码（支持 GBK、GB18030、UTF-8 等）
2. 将内容转为 UTF-8 返回给 AI 处理
3. 写入文件时自动转回原始编码
"""

from __future__ import annotations

import json
import asyncio
import sys
from pathlib import Path
from typing import Any, NamedTuple

_src_dir = Path(__file__).parent.resolve()
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from detector import detect_encoding, detect_file_encoding_details, detect_line_ending, EncodingResult
from converter import decode_to_utf8, read_file_as_utf8, write_file_from_utf8, is_encoding_supported
from encoding_store import (
    store_encoding, get_encoding, has_encoding, get_all_encodings, get_fresh_encoding,
)

server = Server("encoding-server")


def _error(error: str) -> list[TextContent]:
    """快捷构造错误响应"""
    return [TextContent(type="text", text=json.dumps({"success": False, "error": error}, ensure_ascii=False))]


def _resolve_encoding(file_path_str: str, specified: str | None = None) -> tuple[str, list[TextContent] | None]:
    """确定目标编码，返回 (encoding, None) 或 (placeholder, error_response)"""
    target = specified or get_encoding(file_path_str)
    if not target:
        return "", _error(
            "未指定编码，且该文件没有之前的编码记录。"
            "请先使用 read_file_with_encoding 读取文件，或手动指定 encoding 参数。"
        )
    if not is_encoding_supported(target):
        return "", _error(f"不支持的编码: {target}")
    return target, None


_UTF8_BOM = b'\xef\xbb\xbf'


def _reconcile_utf8_bom(file_path: Path, target_encoding: str) -> tuple[str, list[str]]:
    """以文件实际字节为准：已存在的文件若实际带 UTF-8 BOM，但目标编码是普通 utf-8，
    纠正为 utf-8-sig 以保住 BOM。

    防止调用方误传 encoding='utf-8' 导致写回时不补 EF BB BF、BOM 被静默丢弃，
    进而引发 MSVC C4819 等警告。仅对普通 utf-8 生效：utf-8-sig 本就会补 BOM，
    UTF-16/32/GBK 等与 UTF-8 BOM 无关，均不受影响。新建文件（尚不存在）无既有
    BOM 可核对，按调用方传入的 encoding 办。返回 (纠正后的编码, 警告列表)。
    """
    warnings: list[str] = []
    if target_encoding.lower() != 'utf-8':
        return target_encoding, warnings
    try:
        with open(file_path, 'rb') as f:
            head = f.read(3)
    except OSError:
        # 文件不存在（新建）或不可读：无既有 BOM 可核对，按原 encoding 办
        return target_encoding, warnings
    if head == _UTF8_BOM:
        warnings.append(
            "文件实际带 UTF-8 BOM，写入编码已从 utf-8 纠正为 utf-8-sig 以保留 BOM"
            "（传 encoding='utf-8' 会丢失 BOM，可能引发编译器 C4819 警告）。"
        )
        return 'utf-8-sig', warnings
    return target_encoding, warnings


# ── 工具定义 ──────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="read_file_with_encoding",
            description="读取文件（自动检测编码），返回UTF-8内容。",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"}
                },
                "required": ["path"]
            }
        ),
        Tool(
            name="detect_file_encoding",
            description="只读取前 32KB 探测文件编码与行尾风格，不返回文件内容。"
                        "用于在不需要全文时快速获知编码和换行符(CRLF/LF)。",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"}
                },
                "required": ["path"]
            }
        ),
        Tool(
            name="write_file_with_encoding",
            description="写入文件（自动转回原始编码）。如需局部修改，请优先使用 edit_file_with_encoding。",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件绝对路径"},
                    "content": {"type": "string", "description": "文件内容"},
                    "encoding": {"type": "string", "description": "目标编码（可选，默认使用之前检测的编码）"}
                },
                "required": ["path", "content"]
            }
        ),
        Tool(
            name="edit_file_with_encoding",
            description="局部修改文件(字符串替换),适合小修改。",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "old_string": {"type": "string", "description": "旧文本"},
                    "new_string": {"type": "string", "description": "新文本"},
                    "encoding": {"type": "string", "description": "编码(可选)"},
                    "replace_all": {"type": "boolean", "description": "替换所有匹配项"},
                    "match_line_endings": {
                        "type": "boolean",
                        "description": "行尾容错(可选,默认 false)。开启后:若 old_string 逐字节匹配失败,"
                                       "会按文件主流行尾(CRLF/LF)归一化 old_string 重试一次,new_string 同步按文件行尾写回。"
                                       "混合行尾或纯 CR 文件不自动归一化。默认关闭以保持字节精确匹配契约。"
                    },
                    "match_indent": {
                        "type": "boolean",
                        "description": "前导缩进容错(可选,默认 false)。开启后:若逐字节匹配与行尾容错均失败,"
                                       "会按“逐行去掉前导空白后的内容 + 相对缩进层级”比对整行区段,"
                                       "容忍深层 tab/空格缩进的计数偏差。命中后写回 new_string 时,"
                                       "用文件该区域实际前导空白逐行替换,保留 tab/空格风格与缩进深度;"
                                       "new_string 多出的行继承末行缩进,空行保持为空。"
                                       "仅整行对齐的匹配参与;多义(去前导空白后仍多处内容相同)会报错,"
                                       "要求更唯一的 old_string。对 CRLF/LF 行尾差异同样有效。"
                    }
                },
                "required": ["path", "old_string", "new_string"]
            }
        ),
        Tool(
            name="get_file_encoding",
            description="查询文件的编码记录。",
            inputSchema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"]
            }
        ),
        Tool(
            name="list_all_encodings",
            description="列出所有编码记录。",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
    ]


# ── 处理函数 ──────────────────────────────────────────────

def _str_arg(arguments: dict[str, Any], key: str) -> str:
    """从 arguments 中取 string 参数"""
    val = arguments[key]
    if not isinstance(val, str):
        raise ValueError(f"参数 {key} 必须是字符串")
    return val


def _optional_str_arg(arguments: dict[str, Any], key: str) -> str | None:
    """从 arguments 中取可选 string 参数"""
    val = arguments.get(key)
    if val is None:
        return None
    if not isinstance(val, str):
        raise ValueError(f"参数 {key} 必须是字符串")
    return val


def _bool_arg(arguments: dict[str, Any], key: str, default: bool = False) -> bool:
    """从 arguments 中取 bool 参数"""
    val = arguments.get(key, default)
    if not isinstance(val, bool):
        raise ValueError(f"参数 {key} 必须是布尔值")
    return val


def _probe_and_cache_encoding(file_path: Path) -> tuple[EncodingResult, str]:
    """探测文件编码与行尾风格（只读前 32KB）并写入缓存，返回 (结果, 行尾风格)。

    供 detect 工具与 get_file_encoding 缓存未命中时共用，保证“探测即缓存”的单一路径。
    """
    result, line_ending = detect_file_encoding_details(file_path)
    store_encoding(str(file_path), result.encoding, result.confidence)
    return result, line_ending


async def handle_detect_file_encoding(arguments: dict[str, Any]) -> list[TextContent]:
    try:
        file_path = Path(_str_arg(arguments, "path")).resolve()

        if not file_path.exists():
            return _error(f"文件不存在: {file_path}")

        result, line_ending = _probe_and_cache_encoding(file_path)

        return [TextContent(type="text", text=json.dumps({
            "success": True,
            "path": str(file_path),
            "encoding": result.encoding,
            "confidence": result.confidence,
            "line_ending": line_ending,
        }, ensure_ascii=False))]

    except Exception as e:
        return _error(str(e))


async def handle_read_file(arguments: dict[str, Any]) -> list[TextContent]:
    try:
        file_path = Path(_str_arg(arguments, "path")).resolve()

        if not file_path.exists():
            return _error(f"文件不存在: {file_path}")

        # 单次读取文件，同时用于检测和转换
        with open(file_path, 'rb') as f:
            raw_data = f.read()

        if not raw_data:
            store_encoding(str(file_path), 'utf-8')
            return [TextContent(type="text", text=json.dumps({
                "success": True, "path": str(file_path),
                "encoding": "utf-8", "confidence": 1.0, "content": "",
            }, ensure_ascii=False))]

        # 命中未改动缓存(自上次检测以来 mtime/size 未变)则跳过检测,直接复用编码。
        # 大文件检测是大头(2MB GBK ~138ms),避免每次 read 都重算。
        cached = get_fresh_encoding(str(file_path))
        if cached is not None:
            encoding, confidence = cached
        else:
            detection = detect_encoding(raw_data)
            encoding, confidence = detection.encoding, detection.confidence

        content, warnings = decode_to_utf8(raw_data, encoding)

        store_encoding(str(file_path), encoding, confidence)

        result: dict[str, Any] = {
            "success": True,
            "path": str(file_path),
            "encoding": encoding,
            "confidence": confidence,
            "content": content,
        }
        if warnings:
            result["warnings"] = warnings
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

    except Exception as e:
        return _error(str(e))


def _to_lf(s: str) -> str:
    """把字符串的行尾统一成 LF（CRLF 和孤立 CR 都折成 LF）。"""
    return s.replace('\r\n', '\n').replace('\r', '\n')


def _to_crlf(s: str) -> str:
    """把字符串的行尾统一成 CRLF（先把 CRLF 折成 LF 防止重复补 \\r，再把 \\n 补成 \\r\\n）。"""
    return s.replace('\r\n', '\n').replace('\n', '\r\n')


def _align_to_file_line_ending(s: str, content: str) -> str:
    """把 s 的换行归一化成 content（整份文件）的主流行尾。

    纯 CRLF 文件：把 s 的换行统一成 CRLF；纯 LF 文件：统一成 LF；
    混合行尾 / 孤立 CR / 无换行：原样返回，不归一化（避免破坏混合行尾文件）。
    _to_crlf / _to_lf 都先把 CRLF 折成 LF 再统一，故对 s 内已有的混合换行也能
    正确归一，且对已经全是指定行尾的串幂等——容错路径已归一化的 new 再过一次不变。
    """
    le = detect_line_ending(content)
    if le == 'CRLF':
        return _to_crlf(s)
    if le == 'LF':
        return _to_lf(s)
    return s


def _resolve_line_ending_variant(content: str, old_string: str, new_string: str
                                 ) -> tuple[str, str] | None:
    """
    按文件主流行尾把 old_string/new_string 归一化，用于 match_line_endings 容错。

    仅当文件行尾单一（纯 CRLF 或纯 LF）、归一化后的 old_string 确实能在 content 中命中、
    且与原 old_string 不同（确实需要行尾转换）时，返回 (归一化后的 old, 归一化后的 new)；
    否则返回 None（混合/无换行/纯 CR 文件、或归一化后仍不匹配、或无需转换）。
    """
    le = detect_line_ending(content)
    if le == 'CRLF':
        norm_old, norm_new = _to_crlf(old_string), _to_crlf(new_string)
    elif le == 'LF':
        norm_old, norm_new = _to_lf(old_string), _to_lf(new_string)
    else:
        return None
    if norm_old != old_string and norm_old in content:
        return norm_old, norm_new
    return None


def _line_ending_mismatch_hint(content: str, old_string: str) -> str | None:
    """
    仅当 old_string 逐字节不在 content 中、但行尾规范化后能匹配时，
    返回“行尾不一致”的提示；否则返回 None（交由调用方给出通用提示）。
    本函数只做诊断，不做任何替换或行尾转换，落盘字节不受影响。
    """
    if old_string in content:
        return None
    if _to_lf(old_string) not in _to_lf(content):
        return None

    content_is_crlf = '\r\n' in content
    old_has_lf_only = '\n' in old_string.replace('\r\n', '')
    if content_is_crlf and old_has_lf_only:
        return ("未找到要替换的文本：文件使用 CRLF（\\r\\n）换行，但 old_string 用了 LF（\\n）。"
                "请把 old_string 里的换行改成 \\r\\n 后重试。"
                "（匹配为逐字节精确匹配，工具不会自动转换行尾。）")
    if not content_is_crlf and '\r\n' in old_string:
        return ("未找到要替换的文本：文件使用 LF（\\n）换行，但 old_string 用了 CRLF（\\r\\n）。"
                "请把 old_string 里的换行改成 \\n 后重试。"
                "（匹配为逐字节精确匹配，工具不会自动转换行尾。）")
    return ("未找到要替换的文本：疑似行尾不一致（CRLF/LF）。"
            "匹配为逐字节精确匹配，请确保 old_string 的换行与文件完全一致（含 \\r）。")


# ── 前导缩进容错 ──────────────────────────────────────────────
# 精确匹配与行尾容错都失败后启用：按“逐行去掉前导空白后的内容 + 相对缩进层级”
# 比对整行对齐的区段，容忍调用方对深层 tab 缩进的计数偏差。命中后写回 new_string
# 时，用文件该区域每行的实际前导空白逐行替换 new_string 的前导空白，避免把 tab
# 改成空格或反之、或改变缩进深度。仅整行对齐的匹配参与；多义（去前导空白后仍多处
# 内容相同）则报错，要求更唯一的 old_string，绝不擅自替换第一个。

_INDENT_TABSTOP = 8


def _split_leading_ws(line: str) -> tuple[str, str]:
    """拆成 (前导空白, 其余)。前导仅指标记符 tab 与空格。"""
    rest = line.lstrip(' \t')
    return line[:len(line) - len(rest)], rest


def _visual_width(ws: str) -> int:
    """把前导空白按制表位 8 展开成列宽：tab 跳到下一个 8 的倍数，空格记 1。"""
    col = 0
    for ch in ws:
        col = (col // _INDENT_TABSTOP + 1) * _INDENT_TABSTOP if ch == '\t' else col + 1
    return col


def _line_parts_for_cmp(line: str) -> tuple[str, str]:
    """去掉行尾一个 \\r（CRLF 容错），再拆 (前导空白, 内容)。
    返回的前导空白是文件里该行的实际前导；内容用于逐行比较（保留行内与行尾空白）。"""
    if line.endswith('\r'):
        line = line[:-1]
    return _split_leading_ws(line)


def _compute_line_starts(content: str) -> list[int]:
    """每行（按 \\n 切）在原字符串中的起始偏移，长度等于行数。"""
    starts = [0]
    for idx, ch in enumerate(content):
        if ch == '\n':
            starts.append(idx + 1)
    return starts


def _region_sep(content_lines: list[str], k: int, n: int, content: str) -> str:
    """推断被替换区域内部使用的行尾分隔符（CRLF / LF）。
    主流行尾单一时直接采用；混合 / CR / 无换行时按区域内行尾局部推断。"""
    le = detect_line_ending(content)
    if le == 'CRLF':
        return '\r\n'
    if le == 'LF':
        return '\n'
    sample = content_lines[k:k + n - 1] if n > 1 else content_lines[k:k + 1]
    return '\r\n' if any(ln.endswith('\r') for ln in sample) else '\n'


class _IndentMatch(NamedTuple):
    """缩进容错匹配结果。candidate_count: 0=未命中, 1=唯一命中, >1=多义。"""
    matched_old: str | None
    matched_new: str | None
    candidate_count: int


def _resolve_indent_variant(
    content: str, old_string: str, new_string: str
) -> _IndentMatch:
    """前导缩进容错匹配。

    按 (去掉前导空白后的行内容, 相对首行的缩进列宽) 逐行比对 old_string 与
    content 的每个整行对齐区段。仅在唯一命中时返回 (matched_old 区域原文,
    按文件实际缩进改写后的 new_string, 1)；未命中返回 (..., 0)，多义返回
    (..., 候选数 >1)。matched_old 是 content 中的精确子串，供上层 content.replace
    使用。行尾容错自然包含：比对按 \\n 切分并剥除行尾 \\r，CRLF / LF 一视同仁。
    """
    old_lines = old_string.split('\n')
    n = len(old_lines)
    content_lines = content.split('\n')
    m = len(content_lines)
    if n == 0 or m < n:
        return _IndentMatch(None, None, 0)

    old_parts = [_line_parts_for_cmp(ln) for ln in old_lines]
    old_base = _visual_width(old_parts[0][0])
    old_sigs = [(rest, _visual_width(lead) - old_base) for lead, rest in old_parts]

    content_parts = [_line_parts_for_cmp(ln) for ln in content_lines]
    content_widths = [_visual_width(p[0]) for p in content_parts]
    content_rests = [p[1] for p in content_parts]

    candidates: list[int] = []
    for k in range(0, m - n + 1):
        cbase = content_widths[k]
        hit = True
        for i in range(n):
            if content_rests[k + i] != old_sigs[i][0]:
                hit = False
                break
            if content_widths[k + i] - cbase != old_sigs[i][1]:
                hit = False
                break
        if hit:
            candidates.append(k)
            if len(candidates) > 1:
                # 已多义，无需继续扫描；返回 >1 即可触发上层报错
                return _IndentMatch(None, None, len(candidates))

    if len(candidates) != 1:
        return _IndentMatch(None, None, len(candidates))

    k = candidates[0]
    starts = _compute_line_starts(content)
    start = starts[k]
    end = starts[k + n] - 1 if (k + n) < m else len(content)
    # 末行若带 \r（CRLF），把它留在区域外，使其与随后的 \n 配成 CRLF；
    # 否则 matched_new（sep.join 不产生末尾 \r）替换后会把该行降级成 LF。
    if end > start and content[end - 1] == '\r':
        end -= 1
    matched_old = content[start:end]

    # 用文件实际前导空白逐行改写 new_string：保留 tab / 空格风格与缩进深度。
    actual_leads = [content_parts[k + i][0] for i in range(n)]
    sep = _region_sep(content_lines, k, n, content)
    last_lead_idx = len(actual_leads) - 1
    rewritten: list[str] = []
    for j, nline in enumerate(new_string.split('\n')):
        if nline.endswith('\r'):
            nline = nline[:-1]
        body = nline.lstrip(' \t')
        if body == '':
            # 空行 / 纯空白行保持为空，避免在空行上引入行尾空白
            rewritten.append('')
        else:
            idx = j if j < last_lead_idx else last_lead_idx
            rewritten.append(actual_leads[idx] + body)
    matched_new = sep.join(rewritten)
    return _IndentMatch(matched_old, matched_new, 1)


async def handle_edit_file(arguments: dict[str, Any]) -> list[TextContent]:
    try:
        file_path = Path(_str_arg(arguments, "path")).resolve()
        old_string = _str_arg(arguments, "old_string")
        new_string = _str_arg(arguments, "new_string")
        specified_encoding = _optional_str_arg(arguments, "encoding")
        replace_all = _bool_arg(arguments, "replace_all")
        match_line_endings = _bool_arg(arguments, "match_line_endings")
        match_indent = _bool_arg(arguments, "match_indent")

        if not file_path.exists():
            return _error(f"文件不存在: {file_path}")

        target_encoding, err = _resolve_encoding(str(file_path), specified_encoding)
        if err:
            return err
        target_encoding, bom_warning = _reconcile_utf8_bom(file_path, target_encoding)

        content, read_warnings = read_file_as_utf8(file_path, target_encoding)

        # 逐字节精确匹配优先。失败时按开启的容错开关依次重试：
        #   1) match_line_endings：按文件主流行尾归一化 old/new（混合/纯 CR 不归一化）
        #   2) match_indent：按“逐行去前导空白内容 + 相对缩进层级”整行匹配，
        #      写回时用文件实际前导空白逐行替换 new_string 的前导空白。
        # 缩进容错对行尾天然不敏感（按 \n 切分并剥行尾 \r），故即便不同时开启
        # match_line_endings 也能处理 CRLF/LF 差异。
        matched_old, matched_new = old_string, new_string
        if old_string not in content:
            variant = _resolve_line_ending_variant(content, old_string, new_string) if match_line_endings else None
            if variant is None and match_indent:
                im = _resolve_indent_variant(content, old_string, new_string)
                if im.candidate_count == 1 and im.matched_old is not None and im.matched_new is not None:
                    variant = (im.matched_old, im.matched_new)
                elif im.candidate_count > 1:
                    return _error(
                        "前导缩进容错匹配到多处可能的整行区段（去前导空白后内容相同）。"
                        "请提供更唯一的 old_string（增加上下文行），或核对缩进后重试。"
                    )
            if variant is None:
                hint = _line_ending_mismatch_hint(content, old_string)
                msg = hint or "未找到要替换的文本，请检查 old_string 是否准确"
                if match_indent:
                    msg += "（已尝试前导缩进容错，仍未命中唯一整行区段。）"
                return _error(msg)
            matched_old, matched_new = variant

        # 写回前：把 new 的换行归一化成文件主流行尾（纯 CRLF/LF；混合 / 孤立 CR / 无换行不动）。
        # 精确命中与容错命中在此统一处理；容错路径返回的 new 已归一化，再过一次幂等无害。
        matched_new = _align_to_file_line_ending(matched_new, content)

        count = content.count(matched_old)
        if count > 1 and not replace_all:
            return _error(f"要替换的文本出现了 {count} 次。请提供更具体的上下文，或设置 replace_all=true")

        if replace_all:
            new_content = content.replace(matched_old, matched_new)
            actual_count = count
        else:
            new_content = content.replace(matched_old, matched_new, 1)
            actual_count = 1

        write_warnings = write_file_from_utf8(file_path, new_content, target_encoding)
        store_encoding(str(file_path), target_encoding)

        result: dict[str, Any] = {
            "success": True,
            "path": str(file_path),
            "encoding": target_encoding,
            "replacements": actual_count,
            "message": f"已替换 {actual_count} 处，编码: {target_encoding}",
        }
        all_warnings = bom_warning + read_warnings + write_warnings
        if all_warnings:
            result["warnings"] = all_warnings
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

    except Exception as e:
        return _error(str(e))


async def handle_write_file(arguments: dict[str, Any]) -> list[TextContent]:
    try:
        file_path = Path(_str_arg(arguments, "path")).resolve()
        content = _str_arg(arguments, "content")

        target_encoding, err = _resolve_encoding(str(file_path), _optional_str_arg(arguments, "encoding"))
        if err:
            return err
        target_encoding, bom_warning = _reconcile_utf8_bom(file_path, target_encoding)

        # 写回前：若文件已存在，把 content 换行归一化成原文件主流行尾（纯 CRLF/LF），
        # 避免整文件覆盖时把 CRLF 文件写成 LF；新文件无原行尾可参照，原样写。
        if file_path.exists():
            try:
                old_text, _ = read_file_as_utf8(file_path, target_encoding)
                content = _align_to_file_line_ending(content, old_text)
            except Exception:
                pass  # 旧文件读不动则不归一化，原样写（退化到现状行为）

        warnings = bom_warning + write_file_from_utf8(file_path, content, target_encoding)
        store_encoding(str(file_path), target_encoding)

        content_preview = content[:50] + "..." if len(content) > 50 else content
        content_preview = content_preview.replace('\n', ' ').replace('\r', '')

        result: dict[str, Any] = {
            "success": True,
            "path": str(file_path),
            "encoding": target_encoding,
            "size": len(content),
            "preview": content_preview,
        }
        if warnings:
            result["warnings"] = warnings
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

    except Exception as e:
        return _error(str(e))


async def handle_get_encoding(arguments: dict[str, Any]) -> list[TextContent]:
    try:
        file_path = Path(_str_arg(arguments, "path")).resolve()

        if not file_path.exists():
            return _error(f"文件不存在: {file_path}")

        key = str(file_path)
        if has_encoding(key):
            encoding = get_encoding(key)
            return [TextContent(type="text", text=json.dumps({
                "success": True, "path": key, "encoding": encoding
            }, ensure_ascii=False))]

        # 无缓存记录但文件存在：按需探测（只读前 32KB），结果写入缓存供后续复用
        result, _ = _probe_and_cache_encoding(file_path)
        return [TextContent(type="text", text=json.dumps({
            "success": True, "path": key, "encoding": result.encoding
        }, ensure_ascii=False))]

    except Exception as e:
        return _error(str(e))


async def handle_list_encodings(arguments: dict[str, Any]) -> list[TextContent]:
    encodings = get_all_encodings()
    return [TextContent(type="text", text=json.dumps({
        "success": True, "encodings": encodings, "count": len(encodings)
    }, ensure_ascii=False))]


# ── 路由分发 ──────────────────────────────────────────────

_TOOL_HANDLERS: dict[str, Any] = {
    "read_file_with_encoding": handle_read_file,
    "detect_file_encoding": handle_detect_file_encoding,
    "write_file_with_encoding": handle_write_file,
    "edit_file_with_encoding": handle_edit_file,
    "get_file_encoding": handle_get_encoding,
    "list_all_encodings": handle_list_encodings,
}


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    handler = _TOOL_HANDLERS.get(name)
    if handler:
        return await handler(arguments)
    return _error(f"未知工具: {name}")


# ── 启动 ──────────────────────────────────────────────────

async def run_server() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
