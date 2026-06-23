import codecs
import pytest
from detector import detect_encoding, detect_file_encoding, detect_line_ending


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
        assert result.encoding in ("utf-8", "gbk", "gb18030")

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
        assert result.encoding in ("utf-8", "gbk", "gb18030")

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

    def test_safety_net_prefers_gb18030(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory) -> None:
        # detect_file_encoding 的安全网：误判 utf-8 但解不开时，应判 gb18030（超集）
        import detector
        data = self._FOUR_BYTE_TEXT.encode("gb18030")
        monkeypatch.setattr(detector, "detect_encoding",
                            lambda d: detector.EncodingResult(encoding="utf-8", confidence=0.9))
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
        assert result.encoding == "windows-1250"


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
