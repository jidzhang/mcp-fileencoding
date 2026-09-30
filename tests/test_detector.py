import codecs
import pytest
from detector import (
    detect_encoding, detect_file_encoding, detect_file_encoding_details, detect_line_ending,
)


# 使用较长的中文文本，避免 charset-normalizer 对短文本误判
_GBK_TEXT = "这是一段用于测试编码检测功能的中文文本，包含了足够多的汉字以提供准确的检测结果。"
_GB18030_TEXT = "这是用于测试GB18030编码检测的中文文本，需要足够长才能准确识别。"


class TestDetectEncoding:
    def test_ascii(self) -> None:
        result = detect_encoding(b"hello world")
        assert result.encoding == "utf-8"
        assert result.confidence == 1.0

    def test_utf8_chinese(self) -> None:
        data = "你好世界这是一段测试文本".encode("utf-8")
        result = detect_encoding(data)
        # 纯 UTF-8 中文必须判 utf-8:被 GB 系顶掉会把中文读成乱码
        assert result.encoding == "utf-8"

    def test_gbk(self) -> None:
        data = _GBK_TEXT.encode("gbk")
        result = detect_encoding(data)
        # gb18030 是 gbk 的超集，检测为 gb18030 也正确
        assert result.encoding in ("gbk", "gb18030")
        assert result.confidence > 0

    def test_utf8_bom(self) -> None:
        data = b'\xef\xbb\xbf' + "你好".encode("utf-8")
        result = detect_encoding(data)
        assert result.encoding == "utf-8-sig"
        assert result.confidence == 1.0

    def test_empty(self) -> None:
        result = detect_encoding(b"")
        assert result.encoding == "utf-8"
        assert result.confidence == 1.0

    def test_gb18030(self) -> None:
        data = _GB18030_TEXT.encode("gb18030")
        result = detect_encoding(data)
        assert result.encoding in ("gbk", "gb18030")

    def test_gbk_decode_succeeds(self) -> None:
        # GBK 编码的字节能被正确解码回原文
        data = _GBK_TEXT.encode("gbk")
        result = detect_encoding(data)
        decoded = data.decode(result.encoding)
        assert decoded == _GBK_TEXT


class TestDetectFileEncoding:
    def test_gbk_file(self, tmp_path: pytest.TempPathFactory) -> None:
        file = tmp_path / "gbk.txt"  # type: ignore[operator]
        file.write_bytes(_GBK_TEXT.encode("gbk"))
        result = detect_file_encoding(file)
        assert result.encoding in ("gbk", "gb18030")

    def test_utf8_file(self, tmp_path: pytest.TempPathFactory) -> None:
        file = tmp_path / "utf8.txt"  # type: ignore[operator]
        file.write_bytes("hello 测试文本内容".encode("utf-8"))
        result = detect_file_encoding(file)
        # 纯 UTF-8 文件必须判 utf-8,不允许 GB 系
        assert result.encoding == "utf-8"

    def test_utf8_bom_file(self, tmp_path: pytest.TempPathFactory) -> None:
        file = tmp_path / "bom.txt"  # type: ignore[operator]
        file.write_bytes(b'\xef\xbb\xbf' + "BOM测试文件编码检测".encode("utf-8"))
        result = detect_file_encoding(file)
        assert result.encoding == "utf-8-sig"

    def test_empty_file(self, tmp_path: pytest.TempPathFactory) -> None:
        file = tmp_path / "empty.txt"  # type: ignore[operator]
        file.write_bytes(b"")
        result = detect_file_encoding(file)
        assert result.encoding == "utf-8"


class TestGb18030Fallback:
    """gb18030 是 gbk 的严格超集：兜底应优先 gb18030，避免把含 4 字节序列的
    gb18030 文件误判为 gbk（gbk 解不开 4 字节序列，却可能落到默认 gbk）。"""

    # 😀 在 gb18030 中是 4 字节序列（9439fc36），gbk 无法编/解码
    _FOUR_BYTE_TEXT = "这是一段足够长的中文内容，用于编码检测验证，结尾带一个四字节字符：\U0001F600"

    def test_four_byte_gb18030_not_mislabeled_as_gbk(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import charset_normalizer
        data = self._FOUR_BYTE_TEXT.encode("gb18030")
        # 该字节流 gbk 解不开（4 字节序列）
        with pytest.raises(UnicodeDecodeError):
            data.decode("gbk")

        # 强制走兜底路径（charset-normalizer 失效）
        monkeypatch.setattr(charset_normalizer, "detect", lambda d: {"encoding": None})
        result = detect_encoding(data)
        assert result.encoding == "gb18030"

    def test_pure_gbk_via_fallback_is_safe_superset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import charset_normalizer
        data = _GBK_TEXT.encode("gbk")
        monkeypatch.setattr(charset_normalizer, "detect", lambda d: {"encoding": None})
        result = detect_encoding(data)
        # 兜底返回 gb18030（超集），而非 gbk
        assert result.encoding == "gb18030"
        # 超集安全性：gbk 文件用 gb18030 解完全等价、回写逐字节一致
        assert data.decode("gb18030") == _GBK_TEXT
        assert data.decode("gb18030").encode("gb18030") == data

    def test_safety_net_prefers_gb18030(self, tmp_path: pytest.TempPathFactory) -> None:
        # detect_file_encoding 的安全网:BOM 命中即判 utf-8-sig,但正文不是 UTF-8
        # (此处为 gb18030 四字节正文)时,应剥 BOM 回退 gb18030(超集)
        data = codecs.BOM_UTF8 + self._FOUR_BYTE_TEXT.encode("gb18030")
        file = tmp_path / "f.h"  # type: ignore[operator]
        file.write_bytes(data)
        result = detect_file_encoding(file)
        assert result.encoding == "gb18030"


class TestSingleByteMisclassification:
    """charset-normalizer 对“大段 ASCII 夹少量中文”的源码常误报为 windows-1250 /
    iso-8859-X 等单字节遗留编码（这类编码能解码任意字节流，_try_decode 毫无区分力）。
    此时应用 GBK 严格校验：GBK 解得开 ⇒ 高位字节确实成对组成中文 ⇒ 优先 GBK。"""

    # 真实 C++ 源码：几乎全是 ASCII，少量中文注释
    _SPARSE_CN_SRC = (
        "// CMD5Checksum implementation\r\n"
        "#include \"stdafx.h\"\r\n"
        "#include \"MD5Checksum.h\"\r\n\r\n"
        "// 直接读取 CStringA 的内部指针，避免多余拷贝\r\n"
        "void CMD5Checksum::Update(const BYTE* p, size_t n) {\r\n"
        "    // 更新内部校验和状态\r\n"
        "    for (size_t i = 0; i < n; ++i) {\r\n"
        "        // 轮转缓冲区并压缩\r\n"
        "    }\r\n"
        "}\r\n"
    )

    def test_mostly_ascii_gbk_not_mislabeled_as_single_byte(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import charset_normalizer
        data = self._SPARSE_CN_SRC.encode("gbk")
        # 强制模拟 charset-normalizer 的典型误报
        monkeypatch.setattr(charset_normalizer, "detect",
                            lambda d: {"encoding": "windows-1250", "confidence": 0.99})
        result = detect_encoding(data)
        assert result.encoding in ("gbk", "gb18030")
        # 必须能正确还原中文（不能是乱码）
        assert data.decode(result.encoding) == self._SPARSE_CN_SRC

    def test_isolated_high_byte_not_forced_to_gbk(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """0x80 是 GBK 非法起始字节：含孤立 0x80 的字节流 GBK 解不开，
        不应被强转为 GBK，应保留 charset-normalizer 的单字节判断。"""
        import charset_normalizer
        data = b"ascii text \x80 more ascii text"
        monkeypatch.setattr(charset_normalizer, "detect",
                            lambda d: {"encoding": "windows-1250", "confidence": 0.99})
        result = detect_encoding(data)
        # 编码名经 codecs.lookup 归一为规范名 cp1250(windows-1250 的规范名)
        assert result.encoding == "cp1250"


class TestDetectLineEnding:
    """行尾风格检测:仅按字节统计,不做任何规范化。"""

    def test_crlf(self) -> None:
        assert detect_line_ending(b"a\r\nb\r\nc") == "CRLF"

    def test_lf(self) -> None:
        assert detect_line_ending(b"a\nb\nc") == "LF"

    def test_lone_cr(self) -> None:
        assert detect_line_ending(b"a\rb\rc") == "CR"

    def test_no_newline(self) -> None:
        assert detect_line_ending(b"no newlines here") == "none"

    def test_mixed_crlf_and_lf(self) -> None:
        assert detect_line_ending(b"a\r\nb\nc") == "mixed"

    def test_mixed_crlf_and_cr(self) -> None:
        assert detect_line_ending(b"a\r\nb\rc") == "mixed"

    def test_empty(self) -> None:
        assert detect_line_ending(b"") == "none"

    def test_crlf_with_trailing_lf_is_mixed(self) -> None:
        assert detect_line_ending(b"line\r\nline\n") == "mixed"


class TestBomOrdering:
    """长 BOM 必须优先于短 BOM 匹配，否则 UTF-32-LE (FF FE 00 00) 会被
    UTF-16-LE (FF FE) 误判。"""

    def test_utf32_le_not_misdected_as_utf16_le(self) -> None:
        data = codecs.BOM_UTF32_LE + "中文内容测试".encode("utf-32-le")
        result = detect_encoding(data)
        assert result.encoding == "utf-32-le"

    def test_utf16_le_detected(self) -> None:
        data = codecs.BOM_UTF16_LE + "中文内容测试".encode("utf-16-le")
        result = detect_encoding(data)
        assert result.encoding == "utf-16-le"

    def test_utf16_be_detected(self) -> None:
        data = codecs.BOM_UTF16_BE + "中文内容测试".encode("utf-16-be")
        result = detect_encoding(data)
        assert result.encoding == "utf-16-be"

    def test_utf32_be_detected(self) -> None:
        data = codecs.BOM_UTF32_BE + "中文内容测试".encode("utf-32-be")
        result = detect_encoding(data)
        assert result.encoding == "utf-32-be"


class TestDetectionPerformance:
    """检测的性能优化:charset-normalizer 只吃有限样本(大文件不再 O(n));
    UTF-8 高位字节走严格解码快路径,完全不进 charset-normalizer。
    GBK/gb18030 不设严格解码快路径——它们对异种 CJK(SJIS/Big5)与孤立字节过于宽松,
    会破坏既有的精细检测策略(见 TestSingleByteMisclassification / TestGb18030Fallback)。"""

    def test_charset_normalizer_only_gets_bounded_sample(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import charset_normalizer
        import detector
        seen: list[int] = []
        real_detect = charset_normalizer.detect

        def spy(d: bytes) -> dict:
            seen.append(len(d))
            return real_detect(d)

        monkeypatch.setattr(charset_normalizer, "detect", spy)
        data = ("中文内容用于检测测试验证稳定可靠" * 5000).encode("gbk")
        assert len(data) > detector._CN_SAMPLE
        detector.detect_encoding(data)
        assert seen, "charset-normalizer 应至少被调用一次"
        assert max(seen) <= detector._CN_SAMPLE

    def test_utf8_high_byte_skips_charset_normalizer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import charset_normalizer
        called: list[int] = []
        monkeypatch.setattr(charset_normalizer, "detect",
                            lambda d: called.append(1) or {"encoding": None})
        data = ("中文内容用于检测测试验证" * 100).encode("utf-8")
        result = detect_encoding(data)
        assert result.encoding == "utf-8"
        assert called == [], "UTF-8 高位字节应走快路径,不应调用 charset-normalizer"

class TestCjkMisreportGuard:
    """纯中文 GBK 短文本被 charset-normalizer 误报为 big5/cp949/shift_jis 等
    严格多字节 CJK 编码时,常用字占比守卫应改判 GB 系(实测「初始化完成」→big5/0.8、
    「系统初始化完毕请继续执行」→cp949/0.8,读出繁体/韩文乱码)。"""

    @pytest.mark.parametrize("phrase", [
        "初始化完成",
        "系统初始化完毕请继续执行",
        "保存配置文件成功",
        "数据库初始化失败",
        "更新界面显示",
        "计算结果保留两位小数",
        "启动后台线程",
    ])
    def test_pure_chinese_gbk_not_misread_as_other_cjk(self, phrase: str) -> None:
        data = phrase.encode("gbk")
        result = detect_encoding(data)
        assert result.encoding in ("gbk", "gb18030")
        # 按判定的编码必须能还原原文(不是乱码)
        assert data.decode(result.encoding) == phrase

    def test_genuine_big5_preserved(self) -> None:
        # 真 big5 繁体文本必须保住 big5 判定:GBK 严格解码大多直接失败(守卫不触发);
        # 个别 GBK 解得开的,其乱码的常用字占比也远低于 big5 正解,不会被改判。
        text = "這是一段用於測試編碼偵測功能的繁體中文文字,包含了足夠多的漢字以提供準確的偵測結果。"
        data = text.encode("big5")
        result = detect_encoding(data)
        assert result.encoding == "big5"

    def test_sparse_chinese_gbk_in_ascii_still_gb(self) -> None:
        # 代码夹注释形态(大段 ASCII + 少量中文)在守卫之外也必须判 GB 系
        text = (
            "int compute(int x) {\\r\\n"
            "    // 初始化完成,开始处理数据\\r\\n"
            "    return x * 2;\\r\\n"
            "}\\r\\n"
        ) * 4
        data = text.encode("gbk")
        result = detect_encoding(data)
        assert result.encoding in ("gbk", "gb18030")
        assert data.decode(result.encoding) == text


class TestPrefixProbeWindow:
    """32KB 探测窗口的前缀容错:窗口边界劈开多字节字符时,严格校验须容忍末尾
    悬空字节(实测曾把稀疏 GBK 大文件判成 windows-1250、大 UTF-8 文件判成
    gb18030/0.5,错误结论入缓存后 read 静默乱码)。"""

    def test_window_cut_inside_gbk_pair(self, tmp_path: pytest.TempPathFactory) -> None:
        # 前 32761 字节 ASCII,其后连续 GBK 汉字跨过 32768:
        # 窗口内已有完整汉字,末字节恰是下一个汉字的引导字节
        head = b"a" * 32761
        cn = "中文测试数据初始化完毕".encode("gbk")  # 12 字 = 24 字节
        file = tmp_path / "f.txt"  # type: ignore[operator]
        file.write_bytes(head + cn + b"b" * 100)
        assert len(head + cn) > 32768

        result, _ = detect_file_encoding_details(file)
        assert result.encoding in ("gbk", "gb18030")

    def test_window_cut_inside_utf8_triple(self, tmp_path: pytest.TempPathFactory) -> None:
        # ASCII 前缀 + 中文 UTF-8 正文,窗口恰好在 3 字节汉字中间截断
        head = b"a" * 32766
        body = ("中文内容测试" * 200).encode("utf-8")
        file = tmp_path / "f.txt"  # type: ignore[operator]
        file.write_bytes(head + body)

        result, _ = detect_file_encoding_details(file)
        assert result.encoding == "utf-8"

    def test_window_dangling_single_lead_defaults_utf8(self, tmp_path: pytest.TempPathFactory) -> None:
        # 窗口(含多读的 1 字节)内只有 1 个悬空高位字节、无任何完整汉字证据:默认 utf-8。
        # 文件实际是 GBK 时,read 解码失败会由自愈机制纠正(见 test_server 的
        # TestReadSelfHeal),detect 的保守结论不会造成静默乱码。
        file = tmp_path / "f.txt"  # type: ignore[operator]
        file.write_bytes(b"a" * 32768 + "中文".encode("gbk") + b"b" * 10)
        result, _ = detect_file_encoding_details(file)
        assert result.encoding == "utf-8"

    def test_small_file_gets_no_prefix_tolerance(self, tmp_path: pytest.TempPathFactory) -> None:
        # 文件整个落在窗口内:末尾悬空引导字节不是“被截断”而是文件真实内容,
        # 严格校验失败就是失败,不得以前缀容错放宽
        file = tmp_path / "f.txt"  # type: ignore[operator]
        file.write_bytes(b"a" * 32765 + b"\xd6\xd0\xd6")  # 恰 32768 字节,末字节悬空

        result, _ = detect_file_encoding_details(file)
        assert result.encoding != "gbk"

    def test_window_split_crlf_reports_crlf(self, tmp_path: pytest.TempPathFactory) -> None:
        # 9 字节/行,32768 = 3640 行 + 8 字节:窗口末字节恰为 \r,配对 \n 在窗外。
        # 应补读 1 字节配对完整,纯 CRLF 文件不误报 mixed。
        line = b"1234567\r\n"
        file = tmp_path / "f.txt"  # type: ignore[operator]
        file.write_bytes(line * 4000)

        result, line_ending = detect_file_encoding_details(file)
        assert result.encoding == "utf-8"
        assert line_ending == "CRLF"

    def test_window_trailing_cr_without_lf(self, tmp_path: pytest.TempPathFactory) -> None:
        # 窗口末字节是 \r 但窗外紧邻字节不是 \n:该 \r 是真实的孤立 \r,计为 mixed
        line = b"1234567\r\n"
        file = tmp_path / "f.txt"  # type: ignore[operator]
        file.write_bytes(line * 3640 + b"1234567\r" + b"XYZ" * 10)

        result, line_ending = detect_file_encoding_details(file)
        assert line_ending == "mixed"

    def test_ascii_prefix_gbk_tail_still_utf8(self, tmp_path: pytest.TempPathFactory) -> None:
        # 前 32KB 全 ASCII、尾部 GBK:只看前缀应判 utf-8(detect 看不到尾部高字节;
        # read 的解码失败自愈负责纠正)
        prefix = b"ascii line\r\n" * 3000
        tail = "尾部中文内容用于制造差异".encode("gbk")
        file = tmp_path / "big.txt"  # type: ignore[operator]
        file.write_bytes(prefix + tail)

        result, _ = detect_file_encoding_details(file)
        assert result.encoding == "utf-8"


class TestSafetyNetBomGbk:
    """安全网:UTF-8 BOM + GBK 正文的拼接体(非合法 UTF-8)应回退 GB 系,
    而不是按 BOM 判 utf-8-sig 后把裸解码异常抛给调用方。"""

    def test_bom_plus_gbk_body_falls_back_to_gb(self, tmp_path: pytest.TempPathFactory) -> None:
        file = tmp_path / "bom_gbk.txt"  # type: ignore[operator]
        file.write_bytes(b"\xef\xbb\xbf" + "正文中文内容,足够长以供校验稳定。".encode("gbk"))
        result = detect_file_encoding(file)
        # gbk 优先(与单字节守卫一致的优先序);gb18030 是超集,标签差异不影响解码
        assert result.encoding == "gbk"

    def test_bom_plus_valid_utf8_unaffected(self, tmp_path: pytest.TempPathFactory) -> None:
        file = tmp_path / "bom_utf8.txt"  # type: ignore[operator]
        file.write_bytes(b"\xef\xbb\xbf" + "正文中文内容,足够长以供校验稳定。".encode("utf-8"))
        result = detect_file_encoding(file)
        assert result.encoding == "utf-8-sig"
