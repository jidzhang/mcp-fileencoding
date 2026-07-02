# MCP FileEncoding

MCP 服务器，解决 AI 编码助手在 Windows 下读写 GBK/GB18030 等非 UTF-8 文件时乱码的问题。

读取时自动检测编码并转为 UTF-8 返回给 AI，写入时自动转回原始编码，对 AI 完全透明。

## 背景

Windows 中文环境下，很多项目（C/C++、Lisp 等）的源文件使用 GBK 编码保存。AI 编码助手默认用 UTF-8 读取这些文件，导致中文注释和字符串变成乱码。本 MCP 在读写文件时自动处理编码转换，让 AI 能正确处理非 UTF-8 文件。

## 支持的编码

- UTF-8 / UTF-8 BOM
- GBK / GB2312
- GB18030
- 其他 Python `codecs` 支持的编码

## 安装

```bash
git clone https://github.com/jidzhang/mcp-fileencoding.git
cd mcp-fileencoding
pip install -r requirements.txt
```

## 配置

### Claude Code

```bash
claude mcp add fileencoding -- python /path/to/mcp-fileencoding/src/server.py
```

### Claude Desktop / Cursor / 其他 MCP 客户端

在 MCP 配置文件中添加（配置文件路径因客户端而异，参考对应客户端文档）：

```json
{
  "mcpServers": {
    "fileencoding": {
      "command": "python",
      "args": ["/path/to/mcp-fileencoding/src/server.py"]
    }
  }
}
```

## 使用方法

配置完成后，AI 会自动获得以下 6 个工具。

### 工具列表

| 工具 | 说明 |
|------|------|
| `read_file_with_encoding` | 读取文件，自动检测编码，返回 UTF-8 内容 |
| `detect_file_encoding` | 只读前 32KB 探测编码与行尾风格（CRLF/LF），不返回文件内容 |
| `write_file_with_encoding` | 写入文件，自动转回原始编码 |
| `edit_file_with_encoding` | 局部替换文件内容（字符串替换），支持 `match_line_endings` 行尾容错 |
| `get_file_encoding` | 查询文件编码记录；无记录时按需探测并缓存 |
| `list_all_encodings` | 列出所有已记录的编码 |

### 方式一：PreToolUse Hook（推荐）

通过 Claude Code 的 Hook 机制，在 AI 每次调用 Read/Write/Edit 工具时自动检查文件类型并提示使用 MCP。比系统提示词更可靠，多轮对话中不会失效。

在项目根目录创建 `.claude/settings.json`：

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Read|Write|Edit",
        "hooks": [
          {
            "type": "prompt",
            "prompt": "检查 $ARGUMENTS 中的文件路径，如果文件扩展名是 .cpp、.h 或 .lsp，则：\n- 对于 Read 操作：使用 mcp__fileencoding__read_file_with_encoding 代替 Read 工具\n- 对于 Edit 操作：使用 mcp__fileencoding__edit_file_with_encoding 代替 Edit 工具\n- 对于 Write 操作：使用 mcp__fileencoding__write_file_with_encoding 代替 Write 工具\n\n返回 JSON: {\"hookSpecificOutput\": {\"hookEventName\": \"PreToolUse\", \"additionalContext\": \"提示信息\"}}"
          }
        ]
      }
    ]
  }
}
```

按需修改匹配的文件扩展名（`.cpp`、`.h`、`.lsp` 等）。

### 方式二：系统提示词

在 Claude Code 中通过 `--system-prompt` 参数或项目 `CLAUDE.md` 文件添加提示词：

```bash
claude --system-prompt "在读取和修改 .cpp/.h/.lsp/.txt 等文本文件时，使用 fileencoding MCP。.py/.js/.html 等文件不需要使用。其他文件一般不需要使用，只有遇到读取文本乱码后才尝试使用。"
```

**注意**：系统提示词在长对话中可能被 AI 忽略，PreToolUse Hook 是更可靠的选择。

### 工作流程

以编辑一个 GBK 编码的 `.cpp` 文件为例：

1. AI 调用 `read_file_with_encoding` 读取文件 → 自动检测为 GBK → 返回 UTF-8 内容给 AI
2. AI 理解内容后，调用 `edit_file_with_encoding` 修改 → 自动用 GBK 写回文件
3. 文件编码保持不变，不会破坏其他工具的兼容性

### 注意事项

- 编码记录存储在内存中，MCP 服务器重启后清空
- `get_file_encoding` 在无缓存记录时会自动探测（只读前 32KB）并写入缓存；`write`/`edit` 成功后也会写入缓存
- 写入或编辑文件时，如果既无编码记录又未指定 `encoding` 参数，会报错要求显式指定
- 只需知道编码和换行符、不需要文件内容时，用 `detect_file_encoding` 比 `read_file_with_encoding` 更省 token（不返回内容）
- `edit_file_with_encoding` 默认逐字节精确匹配 old_string（含换行符）。若 old_string 的换行与文件不一致（例如 AI 用 LF 拼接而文件是 CRLF），可设置 `match_line_endings: true` 让工具按文件主流行尾自动归一化 old_string 和 new_string；混合行尾文件不自动归一化，仍需手动对齐
- 检测基于文件内容，短文本可能不够准确，建议文件内容不少于几十个汉字

## 开发

### 安装开发依赖

```bash
pip install -r requirements.txt
pip install pytest pyright
```

### 运行测试

```bash
python -m pytest tests/ -v
```

### 类型检查

```bash
npx pyright src/
```

项目使用 pyright strict 模式，所有源码类型检查必须零错误通过。

### 项目结构

```
src/
├── server.py          # MCP 服务器入口，工具定义和请求处理
├── detector.py        # 编码检测（charset-normalizer + GBK 回退）
├── converter.py       # 编码转换（字节 ↔ UTF-8）
└── encoding_store.py  # 内存编码记录存储
tests/
├── test_server.py     # 服务器 handler 测试
├── test_detector.py   # 编码检测测试
├── test_converter.py  # 编码转换测试
└── test_encoding_store.py  # 存储模块测试
```

## 依赖

- Python >= 3.10
- mcp >= 1.0.0
- charset-normalizer >= 3.0.0

## License

MIT
