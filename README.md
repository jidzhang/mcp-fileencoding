# MCP FileEncoding

MCP 服务器，解决 AI 编码助手在 Windows 下读写 GBK/GB18030 等非 UTF-8 文件时乱码的问题。

读取时自动检测编码并转为 UTF-8 返回给 AI，写入时自动转回原始编码，对 AI 完全透明。

## 支持的编码

- UTF-8 / UTF-8 BOM
- GBK / GB2312
- GB18030
- 其他 Python `codecs` 支持的编码

## 安装

```bash
pip install -r requirements.txt
```

## 配置

### Claude Code

```bash
claude mcp add fileencoding -- python /path/to/mcp-fileencoding/src/server.py
```

### 其他 MCP 客户端

在 MCP 配置中添加：

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

## 工具列表

| 工具 | 说明 |
|------|------|
| `read_file_with_encoding` | 读取文件，自动检测编码，返回 UTF-8 内容 |
| `write_file_with_encoding` | 写入文件，自动转回原始编码 |
| `edit_file_with_encoding` | 局部替换文件内容（字符串替换） |
| `get_file_encoding` | 查询文件的编码记录 |
| `list_all_encodings` | 列出所有已记录的编码 |

## 工作原理

1. **读取**：读取文件原始字节 → 自动检测编码 → 转为 UTF-8 → 记录编码
2. **写入**：根据之前记录的编码 → 将 UTF-8 内容转回原始编码 → 写入文件

编码记录存储在内存中，MCP 服务器重启后清空。

## 使用建议

在系统提示词中引导 AI 按需使用：

> 在读取和修改 .cpp/.h/.lsp/.txt 等文本文件时，使用 fileencoding MCP。.py/.js/.html 等文件不需要使用。其他文件一般不需要使用，只有遇到读取文本乱码后才尝试使用。

## 依赖

- Python >= 3.10
- mcp >= 1.0.0
- charset-normalizer >= 3.0.0

## License

MIT
