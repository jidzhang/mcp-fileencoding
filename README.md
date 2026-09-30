# MCP FileEncoding

MCP 服务器，解决 AI 编码助手在 Windows 下读写 GBK/GB18030 等非 UTF-8 文件时乱码的问题。

读取时自动检测编码并转为 UTF-8 返回给 AI，写入时自动转回原始编码，对 AI 完全透明。

不止编码：写入/编辑时自动保持文件的缩进风格（tab/空格与缩进深度）和换行符（CRLF/LF）与原文件一致——AI 拼错行尾、数错缩进是编辑类工具的常见翻车点，本工具替它兜住。

```text
一个 GBK 编码、CRLF 行尾的 .cpp 文件，内容是：// 计算楼层净高

用 UTF-8 直接读取：  // <EF><BF><BD><EF><BF><BD>...（一堆替换符，中文全毁）
用本 MCP 读取：      // 计算楼层净高    ← 自动检测 GBK，转 UTF-8 返回
编辑写回：            LF 拼接的新内容自动转 CRLF，缩进风格原样保留，编码仍是 GBK
```

## 背景

Windows 中文环境下，很多项目（C/C++、Lisp 等）的源文件使用 GBK 编码保存。AI 编码助手默认用 UTF-8 读取这些文件，导致中文注释和字符串变成乱码。本 MCP 在读写文件时自动处理编码转换，让 AI 能正确处理非 UTF-8 文件。

## 支持的编码

- UTF-8 / UTF-8 BOM
- GBK / GB2312
- GB18030
- 其他 Python `codecs` 支持的编码

## 安装

### 方式一：让 AI 代装（推荐）

如果你的 AI 助手（Claude Code 等）已经能联网执行命令，直接把下面这段话发给它，它会完成克隆、装依赖、注册 MCP 并验证：

```text
帮我安装 mcp-fileencoding：从 GitHub 克隆 https://github.com/jidzhang/mcp-fileencoding，
pip 安装依赖，用 claude mcp add 把 src/server.py 注册为名为 fileencoding 的 MCP 服务器，
然后用 claude mcp list 验证已连接。
```

也可以让它先读一遍本 README 再动手——AI 照着文档装，成功率更高。

### 方式二：手动安装

前置要求：Python >= 3.10（`python --version` 确认）。

```bash
git clone https://github.com/jidzhang/mcp-fileencoding.git
cd mcp-fileencoding
pip install -r requirements.txt
```

## 配置

### Claude Code

```bash
claude mcp add fileencoding -- python D:/path/to/mcp-fileencoding/src/server.py
```

Windows 路径建议用正斜杠（避免转义问题），反斜杠也可以。下文的 `/path/to/...` 占位符同样替换为你的实际路径。

配置完成后验证：

```bash
claude mcp list        # 应看到 fileencoding 已连接
```

也可以在会话里让 AI 对一个 GBK 文件调用 `detect_file_encoding`，能返回 `gbk` 即安装成功。

### Claude Desktop / Cursor / 其他 MCP 客户端

在 MCP 配置文件中添加（配置文件路径因客户端而异，参考对应客户端文档）：

```json
{
  "mcpServers": {
    "fileencoding": {
      "command": "python",
      "args": ["D:/path/to/mcp-fileencoding/src/server.py"]
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
| `edit_file_with_encoding` | 局部替换文件内容（字符串替换），支持 `match_line_endings` 行尾容错与 `match_indent` 前导缩进容错 |
| `get_file_encoding` | 查询文件编码记录；无记录时按需探测并缓存 |
| `list_all_encodings` | 列出所有已记录的编码 |

### 让 AI 用起来：两种方式

装好 MCP 只是让工具可用——AI 并不知道什么时候该用它们。两种方式让 AI 在读写非 UTF-8 文件时主动选择本 MCP：

#### 方式一：PreToolUse Hook（推荐）

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

#### 方式二：系统提示词

在项目的 `CLAUDE.md` 文件里加一段：

```markdown
在读取和修改 .cpp/.h/.lsp 等文本文件时，使用 fileencoding MCP。.py/.js/.html 等文件不需要使用。
```

或者启动时追加提示词（注意用 `--append-system-prompt` 追加模式；`--system-prompt` 会整体替换 Claude Code 内置系统提示词，导致其行为异常，勿用）：

```bash
claude --append-system-prompt "在读取和修改 .cpp/.h/.lsp 等文本文件时，使用 fileencoding MCP。.py/.js/.html 等文件不需要使用。"
```

**注意**：提示词方式在长对话中可能被 AI 忽略，PreToolUse Hook 是更可靠的选择。

### 工作流程

以编辑一个 GBK 编码的 `.cpp` 文件为例：

1. AI 调用 `read_file_with_encoding` 读取文件 → 自动检测为 GBK → 返回 UTF-8 内容给 AI
2. AI 理解内容后，调用 `edit_file_with_encoding` 修改 → 自动用 GBK 写回文件
3. 文件编码保持不变，不会破坏其他工具的兼容性

## 行为细节

### 基本行为

- **编码无损**：读写间自动完成编码往返，文件编码保持不变，不破坏其他工具（编辑器、编译器）的兼容性
- **缩进一致**：`edit` 的 `match_indent` 容错匹配 + 命中后按文件该区域实际前导空白写回，AI 数错 tab/空格不会污染缩进风格
- **行尾一致**：写回内容按文件主流行尾（纯 CRLF/LF）自动归一化，AI 用 LF 拼接的内容写 CRLF 文件不会混入杂行尾

- 编码记录存储在内存中，MCP 服务器重启后清空
- 编码记录带新鲜度校验（mtime/size）：`get_file_encoding` 无记录或文件已改动时重新探测；`write`/`edit` 未显式指定 `encoding` 时，若文件已被外部工具改过（如另存为其他编码），会先重新探测再读写，不拿旧编码处理新内容
- `read`/`edit` 遇到编码判定与文件实际字节不符（如大文件前 32KB 全 ASCII 导致探测偏差、外部工具换过编码、UTF-8 BOM 拼接 GBK 正文的异常文件）时，自动按全文重新检测后重试一次并给出提示，而不是直接抛解码错误
- `encoding` 参数接受编码别名（`utf8`/`UTF-8`/`utf_8_sig`/`cp936` 等），会自动归一化为规范名；带 BOM 的文件即使传 `utf-8` 别名也会保住 BOM
- 写入或编辑文件时，如果既无编码记录又未指定 `encoding` 参数，会报错要求显式指定
- 只需知道编码和换行符、不需要文件内容时，用 `detect_file_encoding` 比 `read_file_with_encoding` 更省 token（不返回内容）
- 检测基于文件内容，短文本可能不够准确，建议文件内容不少于几十个汉字

### edit/write 的容错与归一化

- **行尾归一化**：`edit` 写回 new_string、`write` 写回 content 时，按文件主流行尾（纯 CRLF/LF）自动归一化换行——AI 用 LF 拼多行内容写 CRLF 文件时自动转成 CRLF，不会把 LF 混入 CRLF 文件。混合行尾、孤立 CR、无换行文件不归一化，保持原样；`write` 对新建文件（无原行尾可参照）也不归一化
- **`match_line_endings`**：`edit` 默认逐字节精确匹配 old_string（含换行符）。若 old_string 的换行与文件不一致（例如 AI 用 LF 拼接而文件是 CRLF），设为 `true` 让工具按文件主流行尾归一化 old_string 以命中（new_string 的写回归一化已默认开启，无需此开关）；混合行尾文件不自动归一化，仍需手动对齐
- **`match_indent`**：深层 tab/空格缩进难以精确数对时，设为 `true`——逐字节匹配与行尾容错均失败后，工具按"逐行去掉前导空白后的内容 + 相对缩进层级"整行匹配，容忍缩进计数偏差。命中后写回 new_string 时用文件该区域实际前导空白逐行替换（保留 tab/空格风格与缩进深度，new_string 多出的行继承末行缩进）。多义（去前导空白后仍多处内容相同）会报错，要求更唯一的 old_string；该开关对 CRLF/LF 行尾差异同样有效

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
