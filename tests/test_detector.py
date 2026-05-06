import pytest
from detector import detect_encoding, detect_file_encoding


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
