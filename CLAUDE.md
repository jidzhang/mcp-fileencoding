# CLAUDE.md — mcp-fileencoding

## 本文件定位(与 README 分工)

- **README.md** 面向使用者:安装、配置、接入各 MCP 客户端、工具用法、注意事项。**不在此重复。**
- **本文件** 面向在本仓库改代码的 AI / 协作者:不可违反的数据安全契约、编码检测与缓存的内部机制、架构与关键函数定位、如何安全验证。

需要了解某个工具"怎么调用"请查 README;需要了解"为什么这样实现、改它不能碰什么"请查本文件。

## 数据安全契约(改代码必须遵守)

这是底线,违反会损坏文件。`tests/test_byte_fidelity.py` 是护栏,任何改动必须保持全绿。

1. **字节保真**:同一文件 read → 用相同 content write,落盘字节逐字节一致;edit 只允许改动被替换的子串,其余字节(含全部 CRLF/LF/CR 行尾)逐字节不变。绝不规范化行尾——不把 `\r\n` 折成 `\n`,也不给 `\n` 补 `\r`,孤立 `\r` 原样保留。

2. **不篡改**:目标编码无法表示某字符时,必须抛错拒绝,绝不静默替换成 `?` 或 U+FFFD。见 `src/converter.py` 的 `decode_to_utf8` / `encode_from_utf8`。

3. **UTF-8 BOM 保护**:`edit` / `write` 对**已存在**的带 BOM 文件,以文件实际字节为准——即使调用方误传 `encoding="utf-8"`,也会纠正为 `utf-8-sig` 保住 BOM 并在 `warnings` 提示(见 `src/server.py` 的 `_reconcile_utf8_bom`)。防止丢 BOM 引发 MSVC C4819 等警告。改此逻辑必须同步更新 `tests/test_server.py` 的 BOM 用例。

4. **精确匹配**:`edit` 默认对 `old_string` 逐字节精确匹配(含换行符);多次出现且未设 `replace_all` 时报错拒绝,不擅自替换。`match_line_endings` / `match_indent` 容错开关默认关闭,开启后仅在逐字节匹配失败时作为兜底重试(见下),绝不破坏字节保真契约——容错只影响被替换区,区外字节逐字节不变。

## 编码检测内部机制(`src/detector.py`)

`detect_encoding(data)` 按顺序判定:

1. **BOM 命中**(`_detect_by_bom`):100% 可靠,置信度 1.0。`_BOM_MAP` 中长 BOM 排在短 BOM 前(避免 UTF-32-LE 的 `FF FE 00 00` 被 UTF-16-LE 的 `FF FE` 误命中)。UTF-8 BOM → `utf-8-sig`。
2. **纯 ASCII** → `utf-8`。
3. **UTF-8 快路径**:含高位字节时,先试整段严格 `utf-8` 解码;成功则确为 `utf-8`,直接返回,绕开 charset-normalizer。utf-8 是严格编码,异种编码的高位字节几乎不可能整段凑成合法 utf-8,故此判定无歧义。注:GBK/gb18030 **不**设严格解码快路径——它们过于宽松(会吞孤立 0x80、误吞 SJIS/Big5),会破坏下方既有精细策略。
4. **charset-normalizer**(只喂前 64KB `_CN_SAMPLE`,其判断在几十 KB 后即饱和,喂全量只带来 O(n) 开销)+ `_normalize_encoding` 归一。
5. **单字节遗留编码防护**(`_is_permissive_single_byte`,用 `0x80` 探测):charset-normalizer 对"大段 ASCII 夹少量中文"常误报 windows-1250/iso-8859-X(它们能解码任意字节流)。此时先做 GBK 严格校验(全量),通过则判 `gbk`(0.85)或 `gb18030`(0.8)。
6. **安全网**(`_detect_with_safety_net`):判为 utf-8 但实际解码失败时回退 `gb18030`(gbk 的严格超集)。
7. 最终兜底 `gb18030`(超集,比 gbk 更安全)。

**detect 与 read 的探测范围不同**:`detect_file_encoding` 只读前 32KB,大文件若前缀全 ASCII、尾部才出现高字节可能误判;需要准确结果以 `read_file_with_encoding` 的全文探测为准。(BOM 永远在前 3 字节,BOM 文件两种方式都不会漏判。)

行尾检测 `detect_line_ending` 仅统计,返回 `CRLF`/`LF`/`CR`/`mixed`/`none`,不做任何规范化。

## 编码解析与缓存机制

- **解析顺序**(`server.py` `_resolve_encoding`):显式 `encoding` 参数 > 内存缓存 > 报错(提示先 read 或显式指定)。不支持的编码名报错。
- **缓存**(`src/encoding_store.py`):内存字典,规范化绝对路径 → 编码字符串,上限 1000,超出按插入顺序淘汰(FIFO),无持久化。写入时机:`read`、`detect`、`get_file_encoding` 缓存未命中时、`write`/`edit` 成功后。空文件记为 `utf-8`。
- **转换**(`src/converter.py`):读时按编码剥离 BOM(utf-8 与 utf-8-sig 同样剥离,故读侧对编码传错不敏感);写时按编码补回 BOM。二进制读写、手动拼 BOM 字节,不依赖 Python 文本模式 `encoding=` 的 BOM 自动行为。
- **新鲜度缓存**:`store_encoding` 同时记下文件的 mtime/size 快照;`get_fresh_encoding` 在文件未改动时直接返回已检测编码。`read_file_with_encoding` 命中未改动缓存时跳过检测(避免重复探测),文件 mtime/size 变化则重新检测。`edit`/`write` 不走此路径(用解析出的编码直接解码、写回)。

## 架构与关键函数

| 文件 | 职责 | 关键函数 |
|------|------|---------|
| `src/server.py` | MCP 入口、6 个工具定义与 handler、路由 | `_resolve_encoding`(编码解析:参数>缓存>报错)、`_reconcile_utf8_bom`(写前按实际字节保护 UTF-8 BOM)、`_resolve_line_ending_variant`/`_line_ending_mismatch_hint`(行尾容错与诊断)、`_resolve_indent_variant`(前导缩进容错) |
| `src/detector.py` | 编码与行尾探测 | `detect_encoding`、`_detect_by_bom`、`detect_file_encoding_details`(32KB)、`detect_line_ending` |
| `src/converter.py` | 字节 ↔ UTF-8 转换,手动处理 BOM | `decode_to_utf8`、`encode_from_utf8`、`read_file_as_utf8`、`write_file_from_utf8` |
| `src/encoding_store.py` | 内存编码缓存 | `store_encoding`、`get_encoding`、`get_all_encodings`、`clear_all` |

## 验证

```bash
python -m pytest -q     # 全量测试,必须全绿
npx pyright src/        # strict 模式,源码必须零错误
```

- 测试靠 `pyproject.toml` 的 `pythonpath=["src"]` 解析模块;pyright `include=["src"]` 不分析 tests 目录,故测试文件的导入告警属预期,不代表运行错误。
- 各测试文件职责:`test_byte_fidelity.py`(字节往返与行尾保留契约护栏,含 match_line_endings / match_indent 的字节保真)、`test_server.py`(工具端到端,含 BOM、行尾容错、前导缩进容错、不篡改拒绝)、`test_detector.py`(检测分层逻辑)、`test_converter.py`(转换往返)、`test_encoding_store.py`(缓存增删查淘汰)。

## 当前实现状态备注

- 按需探测:`detect_file_encoding` 工具 + `get_file_encoding` 缓存未命中时探测(32KB)。
- 行尾容错:`edit` 的 `match_line_endings`,精确匹配失败时按文件主流行尾归一化 old/new 重试一次(混合行尾不自动归一化)。
- 前导缩进容错:`edit` 的 `match_indent`,精确匹配与行尾容错均失败后,按"逐行去掉前导空白后的内容 + 相对首行的缩进列宽(制表位 8)"整行比对,容忍深层 tab/空格缩进的计数偏差。命中后写回 new_string 时,用文件该区域每行的实际前导空白逐行替换 new_string 的前导空白(保留 tab/空格风格与缩进深度);new_string 多出的行继承末行缩进,空行保持为空。多义(去前导空白后仍多处内容相同)一律报错,即便 `replace_all=true` 也不擅自批量替换。仅整行对齐的匹配参与(片段式替换走精确匹配);对 CRLF/LF 行尾差异同样有效。实现见 `_resolve_indent_variant`,测试见 `tests/test_server.py::TestEditMatchIndent` 与 `tests/test_byte_fidelity.py::TestEditMatchIndentByteFidelity`。
- UTF-8 BOM 保护:edit/write 写已存在的带 BOM 文件时,即使误传 `encoding="utf-8"` 也会纠正为 `utf-8-sig` 保住 BOM 并提示(`_reconcile_utf8_bom`)。
- 检测提速:UTF-8 走严格解码快路径;charset-normalizer 只喂前 64KB(2MB GBK 检测 ~138ms → ~8ms);`read` 命中未改动缓存时跳过检测。GBK/gb18030 不设严格解码快路径(会破坏异种 CJK 与孤立字节的精细判定)。
