# CLAUDE.md — mcp-fileencoding

## 本文件定位(与 README 分工)

- **README.md** 面向使用者:安装、配置、接入各 MCP 客户端、工具用法、注意事项。**不在此重复。**
- **本文件** 面向在本仓库改代码的 AI / 协作者:不可违反的数据安全契约、编码检测与缓存的内部机制、架构与关键函数定位、如何安全验证。

需要了解某个工具"怎么调用"请查 README;需要了解"为什么这样实现、改它不能碰什么"请查本文件。

## 数据安全契约(改代码必须遵守)

这是底线,违反会损坏文件。`tests/test_byte_fidelity.py` 是护栏,任何改动必须保持全绿。

1. **字节保真**:同一文件 read → 用相同 content write,落盘字节逐字节一致;edit 只允许改动被替换的子串,其余字节(含全部 CRLF/LF/CR 行尾)逐字节不变。**文件原有、未被替换的字节绝不规范化行尾**——不把 `\r\n` 折成 `\n`,也不给 `\n` 补 `\r`,孤立 `\r` 原样保留。但**调用方提供的 new_string / write 的 content(被替换或新写入的部分)**,当文件行尾单一(纯 CRLF 或纯 LF)时,按文件主流行尾归一化换行(纯 CRLF 文件把 LF 转 CRLF、纯 LF 文件把 CRLF 转 LF),避免新内容混入与文件不一致的行尾;混合行尾、孤立 CR、无换行文件不归一化,保持原样。见 `_align_to_file_line_ending`。

2. **不篡改**:目标编码无法表示某字符时,必须抛错拒绝,绝不静默替换成 `?` 或 U+FFFD。见 `src/converter.py` 的 `decode_to_utf8` / `encode_from_utf8`。

3. **UTF-8 BOM 保护**:`edit` / `write` 对**已存在**的带 BOM 文件,以文件实际字节为准——即使调用方误传 `encoding="utf-8"`(或其别名 `utf8`/`UTF-8`,入口已用 `canonical_encoding` 归一,别名不再绕过),也会纠正为 `utf-8-sig` 保住 BOM 并在 `warnings` 提示(见 `src/server.py` 的 `_reconcile_utf8_bom`)。防止丢 BOM 引发 MSVC C4819 等警告。改此逻辑必须同步更新 `tests/test_server.py` 的 BOM 用例。

4. **精确匹配**:`edit` 默认对 `old_string` 逐字节精确匹配(含换行符);多次出现且未设 `replace_all` 时报错拒绝,不擅自替换。`match_line_endings` / `match_indent` 容错开关默认关闭,开启后仅在逐字节匹配失败时作为兜底重试(见下),绝不破坏字节保真契约——容错只影响被替换区,区外字节逐字节不变。

## 编码检测内部机制(`src/detector.py`)

`detect_encoding(data, prefix=False)` 按顺序判定:

1. **BOM 命中**(`_detect_by_bom`):100% 可靠,置信度 1.0。`_BOM_MAP` 中长 BOM 排在短 BOM 前(避免 UTF-32-LE 的 `FF FE 00 00` 被 UTF-16-LE 的 `FF FE` 误命中)。UTF-8 BOM → `utf-8-sig`。
2. **纯 ASCII** → `utf-8`。
3. **UTF-8 快路径**:含高位字节时,先试整段严格 `utf-8` 解码;成功则确为 `utf-8`,直接返回,绕开 charset-normalizer。utf-8 是严格编码,异种编码的高位字节几乎不可能整段凑成合法 utf-8,故此判定无歧义。注:GBK/gb18030 **不**设严格解码快路径——它们过于宽松(会吞孤立 0x80、误吞 SJIS/Big5),会破坏下方既有精细策略。**prefix 模式例外**:前缀窗口下 utf-8 解码成功不再终局(悬空尾字节在"被截断的 UTF-8 序列"与"被截断的 GBK 双字节"间两可),需先与 GB 系仲裁。
4. **charset-normalizer**(只喂前 64KB `_CN_SAMPLE`,其判断在几十 KB 后即饱和,喂全量只带来 O(n) 开销)+ `_normalize_encoding` 归一(未知编码名经 `codecs.lookup` 归一到规范名,如 `windows-1250`→`cp1250`)。
5. **单字节遗留编码防护**(`_is_permissive_single_byte`,用 `0x80` 探测):charset-normalizer 对"大段 ASCII 夹少量中文"常误报 windows-1250/iso-8859-X(它们能解码任意字节流)。此时先做 GBK 严格校验(全量),通过则判 `gbk`(0.85)或 `gb18030`(0.8)。
6. **CJK 多字节仲裁守卫**(`_zh_prefers_gb`):CN 报出 big5/cp949/shift_jis 等严格多字节 CJK 编码(或与快路径矛盾的 utf-8)时不直接采信——这些编码与 GBK 同为双字节结构、字节区间高度重叠,GBK 文件几乎总能被它们"合法"解码成乱码(实测纯中文短文本「初始化完成」→big5、「系统初始化完毕请继续执行」→cp949)。若 GBK 严格解码通过且"常用简体汉字占比"(`_COMMON_ZH_CHARS`,945 字表 + 正则扫描,前后各 64KB 样本封顶)明显更高,判 `gbk`(0.85)。真 big5 繁体文本要么 GBK 解不开、要么 GBK 乱码占比 <0.05,不会被误伤。
7. **安全网**(`detect_with_safety_net`):判为 UTF-8 系但实际解码失败时,剥掉 UTF-8 BOM(若有)按 `gb18030` 校验正文,通过则回退 `gb18030`(0.9)。覆盖 BOM+GBK 拼接体。
8. 最终兜底 `gb18030`(超集,比 gbk 更安全)。

**prefix 前缀容错**(`decode_text(..., prefix=True)`):数据是文件的任意截断前缀时,用增量解码器(`final=False`)做严格校验——末尾可能被窗口截断的多字节序列被缓冲而非报错,中间的非法字节仍报错。`detect_file_encoding_details` 一次读 `_PROBE_WINDOW+1`(32769)字节:窗外还有字节则 prefix=True;窗口末字节恰为 `\r` 且窗外紧邻 `\n` 时补入配对,行尾统计不失真。文件整个落在窗口内则 prefix=False,不做任何容错。

**detect 与 read 的探测范围不同**:`detect_file_encoding` 只读前 32KB,大文件若前缀全 ASCII、尾部才出现高字节仍可能误判(前缀容错只救"截断",救不了"看不到");read 解码失败时由自愈机制(见下)按全文重检兜底。(BOM 永远在前 3 字节,BOM 文件两种方式都不会漏判。)

行尾检测 `detect_line_ending` 仅统计,返回 `CRLF`/`LF`/`CR`/`mixed`/`none`,不做任何规范化。

## 编码解析与缓存机制

- **解析顺序**(`server.py` `_resolve_encoding`):显式 `encoding` 参数(经 `converter.canonical_encoding` 归一为规范名:`utf8`/`UTF-8`→`utf-8`、`utf_8_sig`→`utf-8-sig`、`cp936`→`gbk`,别名不再绕过 BOM 保护等字面量判断)> 新鲜缓存 > 过期重探测 > 报错(提示先 read 或显式指定)。不支持的编码名报错。
- **缓存**(`src/encoding_store.py`):内存字典,规范化绝对路径 → 编码字符串,上限 1000,超出按插入顺序淘汰(FIFO),无持久化。写入时机:`read`、`detect`、`get_file_encoding` 探测时、`write`/`edit` 成功后、read/edit 自愈纠正后。空文件记为 `utf-8`。
- **转换**(`src/converter.py`):读时按编码剥离 BOM(utf-8 与 utf-8-sig 同样剥离,故读侧对编码传错不敏感);写时按编码补回 BOM。二进制读写、手动拼 BOM 字节,不依赖 Python 文本模式 `encoding=` 的 BOM 自动行为。
- **新鲜度缓存**:`store_encoding` 同时记下文件的 mtime/size 快照;`get_fresh_encoding` 在文件未改动时直接返回已检测编码。`read` 命中未改动缓存时跳过检测(避免重复探测);`edit`/`write`/`get_file_encoding` 同样走新鲜度:文件 mtime/size 变化即重新探测,不拿过期编码解码被外部工具换过编码的文件。已知残留:保留时间戳的同尺寸重写(robocopy /copyall、rsync -a)判定不出,需内容指纹才能覆盖,未实现。
- **解码失败自愈**(`server.py` `_decode_with_redetect`,`read`/`edit` 共用):按解析出的编码解码失败时,依次尝试 ① UTF-8 BOM + GB 系正文拼接体(剥 BOM 按 gbk/gb18030 严格解码)② 按全文重新检测(含安全网)后重试;成功则返回内容并在 warnings 说明、纠正缓存,两种尝试都失败才报错。覆盖:detect 的 32KB 前缀误判入缓存、外部换编码但显式传了过期 encoding、BOM+GBK 异常文件。

## 架构与关键函数

| 文件 | 职责 | 关键函数 |
|------|------|---------|
| `src/server.py` | MCP 入口、6 个工具定义与 handler、路由 | `_resolve_encoding`(编码解析:归一化>新鲜缓存>过期重探测>报错)、`_decode_with_redetect`(解码失败自愈)、`_reconcile_utf8_bom`(写前按实际字节保护 UTF-8 BOM)、`_align_to_file_line_ending`(写回时按文件主流行尾归一化 new_string/content)、`_resolve_line_ending_variant`/`_line_ending_mismatch_hint`(行尾容错与诊断)、`_resolve_indent_variant`(前导缩进容错) |
| `src/detector.py` | 编码与行尾探测 | `detect_encoding`(prefix 前缀容错)、`decode_text`(严格/前缀解码)、`detect_with_safety_net`(BOM+GBK 安全网)、`decode_bom_gb_body`(BOM+GB 系拼接体读取,安全网与读侧自愈共用)、`_zh_prefers_gb`(CJK 仲裁守卫)、`_detect_by_bom`、`detect_file_encoding_details`(32KB+1 窗口)、`detect_line_ending` |
| `src/converter.py` | 字节 ↔ UTF-8 转换,手动处理 BOM | `decode_to_utf8`、`encode_from_utf8`、`read_file_as_utf8`、`write_file_from_utf8`、`canonical_encoding`(编码名归一,gb2312/ascii 按项目口径收敛,detector 与 server 共用) |
| `src/encoding_store.py` | 内存编码缓存 | `store_encoding`、`get_encoding`、`get_fresh_encoding`、`get_all_encodings`、`clear_all` |

## 验证

```bash
python -m pytest -q     # 全量测试,必须全绿
npx pyright src/        # strict 模式,源码必须零错误
```

- 测试靠 `pyproject.toml` 的 `pythonpath=["src"]` 解析模块;pyright `include=["src"]` 不分析 tests 目录,故测试文件的导入告警属预期,不代表运行错误。
- 各测试文件职责:`test_byte_fidelity.py`(字节往返与行尾保留契约护栏,含 match_line_endings / match_indent 的字节保真)、`test_server.py`(工具端到端,含 BOM、行尾容错、前导缩进容错、不篡改拒绝)、`test_detector.py`(检测分层逻辑)、`test_converter.py`(转换往返)、`test_encoding_store.py`(缓存增删查淘汰)。

## 当前实现状态备注

- 按需探测:`detect_file_encoding` 工具(每次重探测并覆盖缓存)+ `get_file_encoding` 无记录或记录过期(mtime/size 变化)时探测(32KB)。
- CJK 误判守卫:CN 报 big5/cp949/shift_jis 或与 utf-8 快路径矛盾的 utf-8 时,GBK 严格解码 + 常用字占比(945 字表)仲裁,纯中文 GBK 短文本不再读成繁体/韩文乱码;真 big5 不受影响(GBK 多解不开,解得开的乱码占比 <0.05)。打分只取前后各 64KB 样本,MB 级文件开销封顶(~1ms)。
- 32KB 前缀容错:探测窗口一次读 32769 字节(多读的 1 字节既判断截断,也把可能被劈开的 CRLF 带上行尾统计),窗外还有字节时所有严格校验用增量解码器容忍末尾被截断的多字节序列(中间非法字节仍报错);窗口劈开汉字不再把稀疏 GBK 判成 windows-1250、大 UTF-8 判成 gb18030。窗口内只有悬空单字节、无完整汉字证据时保守判 utf-8,由 read 自愈兜底。
- 解码失败自愈:read/edit 解码失败时按全文重检重试一次(含 BOM+GBK 拼接体剥 BOM 处理),成功则带 warnings 返回并纠正缓存,失败才报错;不再把裸 codec 异常抛给调用方。
- 编码名归一:显式 `encoding` 与检测出的编码名都归一为 codecs 规范名(连字符风格),`utf8`/`utf_8_sig`/`cp936` 等别名不再绕过 BOM 保护与 BOM 剥补表。
- 缓存新鲜度:read/edit/write/get_file_encoding 都走 `get_fresh_encoding`,文件改动即重探测,不拿过期编码处理外部换过编码的文件。残留:保留 mtime 的同尺寸重写判定不出(需内容指纹,未实现)。
- 写回行尾归一化:`edit` 写回 new_string、`write` 写回 content 时,按文件主流行尾(纯 CRLF/LF)归一化换行——AI 用 LF 拼多行内容写 CRLF 文件时自动转成 CRLF,不再混入 LF;混合行尾/孤立 CR/无换行文件不归一化。实现 `_align_to_file_line_ending`:edit 在精确与容错命中汇合点统一处理,write 仅对已存在文件按原行尾归一化、新文件原样写。
- 行尾容错:`edit` 的 `match_line_endings`,精确匹配失败时按文件主流行尾归一化 old/new 重试一次(混合行尾不自动归一化)。
- 前导缩进容错:`edit` 的 `match_indent`,精确匹配与行尾容错均失败后,按"逐行去掉前导空白后的内容 + 相对首行的缩进列宽(制表位 8)"整行比对,容忍深层 tab/空格缩进的计数偏差。命中后写回 new_string 时,用文件该区域每行的实际前导空白逐行替换 new_string 的前导空白(保留 tab/空格风格与缩进深度);new_string 多出的行继承末行缩进,空行保持为空。多义(去前导空白后仍多处内容相同)一律报错,即便 `replace_all=true` 也不擅自批量替换。仅整行对齐的匹配参与(片段式替换走精确匹配);对 CRLF/LF 行尾差异同样有效。实现见 `_resolve_indent_variant`,测试见 `tests/test_server.py::TestEditMatchIndent` 与 `tests/test_byte_fidelity.py::TestEditMatchIndentByteFidelity`。
- UTF-8 BOM 保护:edit/write 写已存在的带 BOM 文件时,即使误传 `encoding="utf-8"` 也会纠正为 `utf-8-sig` 保住 BOM 并提示(`_reconcile_utf8_bom`)。
- 检测提速:UTF-8 走严格解码快路径;charset-normalizer 只喂前 64KB(2MB GBK 检测 ~138ms → ~8ms);`read` 命中未改动缓存时跳过检测。GBK/gb18030 不设严格解码快路径(会破坏异种 CJK 与孤立字节的精细判定)。
