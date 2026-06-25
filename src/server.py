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
from typing import Any

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


async def handle_edit_file(arguments: dict[str, Any]) -> list[TextContent]:
    try:
        file_path = Path(_str_arg(arguments, "path")).resolve()
        old_string = _str_arg(arguments, "old_string")
        new_string = _str_arg(arguments, "new_string")
        specified_encoding = _optional_str_arg(arguments, "encoding")
        replace_all = _bool_arg(arguments, "replace_all")
        match_line_endings = _bool_arg(arguments, "match_line_endings")

        if not file_path.exists():
            return _error(f"文件不存在: {file_path}")

        target_encoding, err = _resolve_encoding(str(file_path), specified_encoding)
        if err:
            return err
        target_encoding, bom_warning = _reconcile_utf8_bom(file_path, target_encoding)

        content, read_warnings = read_file_as_utf8(file_path, target_encoding)

        # 逐字节精确匹配优先。失败时若开启 match_line_endings，按文件主流行尾
        # 归一化 old_string 重试一次（new_string 同步归一化，保证写入行尾与文件一致）。
        # 混合/无换行/纯 CR 的文件不归一化，交由行尾诊断提示。
        matched_old, matched_new = old_string, new_string
        if old_string not in content:
            variant = _resolve_line_ending_variant(content, old_string, new_string) if match_line_endings else None
            if variant is None:
                hint = _line_ending_mismatch_hint(content, old_string)
                return _error(hint or "未找到要替换的文本，请检查 old_string 是否准确")
            matched_old, matched_new = variant

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
