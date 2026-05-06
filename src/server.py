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

from detector import detect_encoding
from converter import decode_to_utf8, read_file_as_utf8, write_file_from_utf8, is_encoding_supported
from encoding_store import store_encoding, get_encoding, has_encoding, get_all_encodings

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
                    "replace_all": {"type": "boolean", "description": "替换所有匹配项"}
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

        detection = detect_encoding(raw_data)
        content, warnings = decode_to_utf8(raw_data, detection.encoding)

        store_encoding(str(file_path), detection.encoding)

        result: dict[str, Any] = {
            "success": True,
            "path": str(file_path),
            "encoding": detection.encoding,
            "confidence": detection.confidence,
            "content": content,
        }
        if warnings:
            result["warnings"] = warnings
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

    except Exception as e:
        return _error(str(e))


async def handle_edit_file(arguments: dict[str, Any]) -> list[TextContent]:
    try:
        file_path = Path(_str_arg(arguments, "path")).resolve()
        old_string = _str_arg(arguments, "old_string")
        new_string = _str_arg(arguments, "new_string")
        specified_encoding = _optional_str_arg(arguments, "encoding")
        replace_all = _bool_arg(arguments, "replace_all")

        if not file_path.exists():
            return _error(f"文件不存在: {file_path}")

        target_encoding, err = _resolve_encoding(str(file_path), specified_encoding)
        if err:
            return err

        content, read_warnings = read_file_as_utf8(file_path, target_encoding)

        if old_string not in content:
            return _error("未找到要替换的文本，请检查 old_string 是否准确")

        count = content.count(old_string)
        if count > 1 and not replace_all:
            return _error(f"要替换的文本出现了 {count} 次。请提供更具体的上下文，或设置 replace_all=true")

        if replace_all:
            new_content = content.replace(old_string, new_string)
            actual_count = count
        else:
            new_content = content.replace(old_string, new_string, 1)
            actual_count = 1

        write_warnings = write_file_from_utf8(file_path, new_content, target_encoding)

        result: dict[str, Any] = {
            "success": True,
            "path": str(file_path),
            "encoding": target_encoding,
            "replacements": actual_count,
            "message": f"已替换 {actual_count} 处，编码: {target_encoding}",
        }
        all_warnings = read_warnings + write_warnings
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

        warnings = write_file_from_utf8(file_path, content, target_encoding)

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

        if has_encoding(str(file_path)):
            encoding = get_encoding(str(file_path))
            return [TextContent(type="text", text=json.dumps({
                "success": True, "path": str(file_path), "encoding": encoding
            }, ensure_ascii=False))]
        else:
            return _error("该文件没有编码记录。请先使用 read_file_with_encoding 读取文件。")

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
