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

    def test_invalid_bytes_refuses_instead_of_replacing(self) -> None:
        data = b'\xff\xfe'  # invalid UTF-8
        # 不篡改原则：无法忠实解码时直接抛错，绝不静默替换为占位符
        with pytest.raises(UnicodeDecodeError):
            decode_to_utf8(data, "utf-8")


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

    def test_unencodable_char_refuses_instead_of_replacing(self) -> None:
        # GBK cannot encode some rare Unicode characters
        # 不篡改原则：无法忠实编码时直接抛错，绝不静默替换为 '?'
        with pytest.raises(UnicodeEncodeError):
            encode_from_utf8("𠀀", "gbk")


class TestFileRoundTrip:
    def test_gbk_round_trip(self, tmp_path: pytest.TempPathFactory) -> None:
        file = tmp_path / "test_gbk.txt"  # type: ignore[operator]
        original = "这是中文内容测试"
        write_file_from_utf8(file, original, "gbk")
        content, warnings = read_file_as_utf8(file, "gbk")
        assert content == original
        assert warnings == []
        # 字节级断言：落盘字节与直接编码完全一致
        assert file.read_bytes() == original.encode("gbk")

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
        # 字节级断言：BOM 被正确写回
        assert raw == b'\xef\xbb\xbf' + original.encode("utf-8")


class TestIsEncodingSupported:
    def test_valid_encodings(self) -> None:
        assert is_encoding_supported("utf-8")
        assert is_encoding_supported("gbk")
        assert is_encoding_supported("gb18030")
        assert is_encoding_supported("utf-8-sig")

    def test_invalid_encoding(self) -> None:
        assert not is_encoding_supported("nonexistent-encoding-xyz")


class TestUtf16Utf32Bom:
    """UTF-16/32 的 BOM 必须读时去除、写时补回，且字节序保持不变（逐字节一致）。"""

    def test_decode_utf16_le_strips_bom(self) -> None:
        data = b'\xff\xfe' + "中文内容".encode("utf-16-le")
        content, _ = decode_to_utf8(data, "utf-16-le")
        assert content == "中文内容"          # 无前导
        assert not content.startswith("﻿")

    @pytest.mark.parametrize("enc,bom", [
        ("utf-16-le", b'\xff\xfe'),
        ("utf-16-be", b'\xfe\xff'),
        ("utf-32-le", b'\xff\xfe\x00\x00'),
        ("utf-32-be", b'\x00\x00\xfe\xff'),
    ])
    def test_round_trip_byte_identity(self, enc: str, bom: bytes) -> None:
        original = bom + "中文\r\n混合\n行尾测试".encode(enc)
        text, _ = decode_to_utf8(original, enc)
        out, _ = encode_from_utf8(text, enc)
        assert out == original
