import pytest
from converter import decode_to_utf8, encode_from_utf8, read_file_as_utf8, write_file_from_utf8, is_encoding_supported


class TestDecodeToUtf8:
    def test_utf8(self) -> None:
        data = "你好世界".encode("utf-8")
        content, warnings = decode_to_utf8(data, "utf-8")
        assert content == "你好世界"
        assert warnings == []

    def test_utf8_bom(self) -> None:
        data = b'\xef\xbb\xbf' + "你好".encode("utf-8")
        content, warnings = decode_to_utf8(data, "utf-8-sig")
        assert content == "你好"
        assert warnings == []

    def test_gbk(self) -> None:
        data = "中文内容".encode("gbk")
        content, warnings = decode_to_utf8(data, "gbk")
        assert content == "中文内容"
        assert warnings == []

    def test_gb18030(self) -> None:
        data = "测试文本".encode("gb18030")
        content, warnings = decode_to_utf8(data, "gb18030")
        assert content == "测试文本"
        assert warnings == []

    def test_invalid_bytes_with_replace(self) -> None:
        data = b'\xff\xfe'  # invalid UTF-8
        content, warnings = decode_to_utf8(data, "utf-8")
        assert "�" in content
        assert len(warnings) == 1


class TestEncodeFromUtf8:
    def test_utf8(self) -> None:
        data, warnings = encode_from_utf8("你好", "utf-8")
        assert data == "你好".encode("utf-8")
        assert warnings == []

    def test_utf8_bom(self) -> None:
        data, warnings = encode_from_utf8("你好", "utf-8-sig")
        assert data.startswith(b'\xef\xbb\xbf')
        assert data[3:] == "你好".encode("utf-8")
        assert warnings == []

    def test_gbk(self) -> None:
        data, warnings = encode_from_utf8("中文", "gbk")
        assert data == "中文".encode("gbk")
        assert warnings == []

    def test_unencodable_char(self) -> None:
        # GBK cannot encode some rare Unicode characters
        data, warnings = encode_from_utf8("𠀀", "gbk")
        assert len(warnings) == 1
        assert b"?" in data


class TestFileRoundTrip:
    def test_gbk_round_trip(self, tmp_path: pytest.TempPathFactory) -> None:
        file = tmp_path / "test_gbk.txt"  # type: ignore[operator]
        original = "这是中文内容测试"
        write_file_from_utf8(file, original, "gbk")
        content, warnings = read_file_as_utf8(file, "gbk")
        assert content == original
        assert warnings == []

    def test_utf8_bom_round_trip(self, tmp_path: pytest.TempPathFactory) -> None:
        file = tmp_path / "test_bom.txt"  # type: ignore[operator]
        original = "BOM 测试"
        write_file_from_utf8(file, original, "utf-8-sig")
        with open(file, "rb") as f:
            raw = f.read()
        assert raw.startswith(b'\xef\xbb\xbf')
        content, warnings = read_file_as_utf8(file, "utf-8-sig")
        assert content == original
        assert warnings == []


class TestIsEncodingSupported:
    def test_valid_encodings(self) -> None:
        assert is_encoding_supported("utf-8")
        assert is_encoding_supported("gbk")
        assert is_encoding_supported("gb18030")
        assert is_encoding_supported("utf-8-sig")

    def test_invalid_encoding(self) -> None:
        assert not is_encoding_supported("nonexistent-encoding-xyz")
