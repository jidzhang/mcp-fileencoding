import json
import asyncio
import pytest
import converter
from encoding_store import clear_all
from server import (
    handle_read_file,
    handle_write_file,
    handle_edit_file,
    handle_get_encoding,
    handle_list_encodings,
    handle_detect_file_encoding,
    call_tool,
)


_GBK_TEXT = "这是一段用于测试编码检测功能的中文文本，包含了足够多的汉字以提供准确的检测结果。"


@pytest.fixture(autouse=True)
def clean_store() -> None:
    clear_all()


def _parse(text: str) -> dict:
    return json.loads(text)


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestDetectFileEncoding:
    def test_detect_gbk_crlf(self, tmp_path: pytest.TempPathFactory) -> None:
        text = "这是足够长的中文内容用于编码检测验证，第一行。\r\n第二行中文内容继续。\r\n"
        file = tmp_path / "f.txt"  # type: ignore[operator]
        file.write_bytes(text.encode("gbk"))

        result = run(handle_detect_file_encoding({"path": str(file)}))
        data = _parse(result[0].text)
        assert data["success"] is True
        assert data["encoding"] in ("gbk", "gb18030")
        assert data["line_ending"] == "CRLF"
        assert "confidence" in data
        # 探测工具不返回内容
        assert "content" not in data

    def test_detect_utf8_lf(self, tmp_path: pytest.TempPathFactory) -> None:
        file = tmp_path / "f.txt"  # type: ignore[operator]
        file.write_bytes("hello 测试文本内容\n第二行\n".encode("utf-8"))

        result = run(handle_detect_file_encoding({"path": str(file)}))
        data = _parse(result[0].text)
        assert data["success"] is True
        assert data["encoding"] in ("utf-8", "gbk", "gb18030")
        assert data["line_ending"] == "LF"

    def test_detect_utf8_bom(self, tmp_path: pytest.TempPathFactory) -> None:
        file = tmp_path / "f.txt"  # type: ignore[operator]
        file.write_bytes(b'\xef\xbb\xbf' + "BOM测试文件编码检测\r\n".encode("utf-8"))

        result = run(handle_detect_file_encoding({"path": str(file)}))
        data = _parse(result[0].text)
        assert data["success"] is True
        assert data["encoding"] == "utf-8-sig"

    def test_detect_empty_file(self, tmp_path: pytest.TempPathFactory) -> None:
        file = tmp_path / "empty.txt"  # type: ignore[operator]
        file.write_bytes(b"")

        result = run(handle_detect_file_encoding({"path": str(file)}))
        data = _parse(result[0].text)
        assert data["success"] is True
        assert data["encoding"] == "utf-8"
        assert data["line_ending"] == "none"

    def test_detect_nonexistent_file(self) -> None:
        result = run(handle_detect_file_encoding({"path": "/nonexistent/detect.txt"}))
        data = _parse(result[0].text)
        assert data["success"] is False
        assert "文件不存在" in data["error"]

    def test_detect_only_reads_prefix(self, tmp_path: pytest.TempPathFactory) -> None:
        # 文件前 32KB 全是 ASCII（含 CRLF），32KB 之后才是 GBK 中文。
        # 只读前缀的探测工具应判 utf-8，看不到尾部的 GBK 字节。
        prefix = (b"ascii line\r\n") * 3000  # 约 33KB，全部 ASCII + CRLF
        assert len(prefix) > 32768
        tail = "尾部中文内容用于制造差异".encode("gbk")
        file = tmp_path / "big.txt"  # type: ignore[operator]
        file.write_bytes(prefix + tail)

        result = run(handle_detect_file_encoding({"path": str(file)}))
        data = _parse(result[0].text)
        assert data["success"] is True
        assert data["encoding"] == "utf-8"
        assert data["line_ending"] == "CRLF"
        assert "content" not in data

    def test_detect_via_call_tool_routing(self, tmp_path: pytest.TempPathFactory) -> None:
        file = tmp_path / "f.txt"  # type: ignore[operator]
        file.write_bytes(_GBK_TEXT.encode("gbk"))

        result = run(call_tool("detect_file_encoding", {"path": str(file)}))
        data = _parse(result[0].text)
        assert data["success"] is True
        assert data["encoding"] in ("gbk", "gb18030")


class TestReadFile:
    def test_read_gbk_file(self, tmp_path: pytest.TempPathFactory) -> None:
        file = tmp_path / "test.txt"  # type: ignore[operator]
        file.write_bytes(_GBK_TEXT.encode("gbk"))

        result = run(handle_read_file({"path": str(file)}))
        data = _parse(result[0].text)
        assert data["success"] is True
        assert data["content"] == _GBK_TEXT
        assert data["encoding"] in ("gbk", "gb18030")

    def test_read_utf8_file(self, tmp_path: pytest.TempPathFactory) -> None:
        file = tmp_path / "test.txt"  # type: ignore[operator]
        file.write_bytes("hello world".encode("utf-8"))

        result = run(handle_read_file({"path": str(file)}))
        data = _parse(result[0].text)
        assert data["success"] is True
        assert data["content"] == "hello world"

    def test_read_nonexistent_file(self) -> None:
        result = run(handle_read_file({"path": "/nonexistent/file.txt"}))
        data = _parse(result[0].text)
        assert data["success"] is False
        assert "文件不存在" in data["error"]


class TestWriteFile:
    def test_write_and_read_back(self, tmp_path: pytest.TempPathFactory) -> None:
        file = tmp_path / "test.txt"  # type: ignore[operator]
        # 先读取以记录编码
        file.write_bytes(_GBK_TEXT.encode("gbk"))
        run(handle_read_file({"path": str(file)}))

        # 写入新内容
        new_text = "这是新写入的中文内容用于测试"
        result = run(handle_write_file({"path": str(file), "content": new_text}))
        data = _parse(result[0].text)
        assert data["success"] is True

        # 验证文件仍是同一编码（gbk/gb18030），内容正确
        detected_enc = data["encoding"]
        content, _ = converter.read_file_as_utf8(file, detected_enc)
        assert content == new_text
        # 字节级断言：落盘字节与按检测编码直接编码一致
        assert file.read_bytes() == new_text.encode(detected_enc)

    def test_write_with_explicit_encoding(self, tmp_path: pytest.TempPathFactory) -> None:
        file = tmp_path / "test.txt"  # type: ignore[operator]
        result = run(handle_write_file({
            "path": str(file),
            "content": "测试",
            "encoding": "gbk",
        }))
        data = _parse(result[0].text)
        assert data["success"] is True
        assert file.read_bytes() == "测试".encode("gbk")

    def test_write_unencodable_char_refuses(self, tmp_path: pytest.TempPathFactory) -> None:
        # 不篡改原则：目标编码无法表示的字符，必须拒绝并报错，绝不静默替换
        file = tmp_path / "test.txt"  # type: ignore[operator]
        file.write_bytes("中文原始内容".encode("gbk"))

        result = run(handle_write_file({
            "path": str(file), "encoding": "gbk", "content": "中文😀内容",
        }))
        data = _parse(result[0].text)
        assert data["success"] is False
        # 文件未被损坏
        assert file.read_bytes() == "中文原始内容".encode("gbk")

    def test_write_utf8_bom_preserved_when_encoding_param_is_plain_utf8(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        # 已存在的带 BOM 文件，调用方误传 encoding="utf-8" 写入 → 应保住 BOM 并提示
        file = tmp_path / "bom.txt"  # type: ignore[operator]
        file.write_bytes(b'\xef\xbb\xbf' + "原始带BOM的UTF-8中文内容，足够长。".encode("utf-8"))

        result = run(handle_write_file({
            "path": str(file), "encoding": "utf-8",
            "content": "新写入的UTF-8中文内容，足够长以供测试验证。",
        }))
        data = _parse(result[0].text)
        assert data["success"] is True
        assert file.read_bytes().startswith(b'\xef\xbb\xbf')
        assert data["encoding"] == "utf-8-sig"
        assert any("utf-8-sig" in w for w in data.get("warnings", []))

    def test_write_new_file_utf8_does_not_add_bom(self, tmp_path: pytest.TempPathFactory) -> None:
        # 回归：新建文件（无既有 BOM 可保）+ encoding="utf-8" → 不应被误加 BOM
        file = tmp_path / "new.txt"  # type: ignore[operator]
        result = run(handle_write_file({
            "path": str(file), "encoding": "utf-8", "content": "新建的UTF-8内容。",
        }))
        data = _parse(result[0].text)
        assert data["success"] is True
        assert file.read_bytes() == "新建的UTF-8内容。".encode("utf-8")


# write 写回归一化：content 换行按原文件主流行尾归一化（纯 CRLF/LF；混合/新文件不动）
class TestWriteLineEndingNormalization:
    """write 写回时把 content 换行归一化成原文件主流行尾（纯 CRLF/LF）；
    混合行尾文件与新文件（无原行尾可参照）不归一化，保持原样。"""

    def test_write_lf_content_to_crlf_file_becomes_crlf(self, tmp_path: pytest.TempPathFactory) -> None:
        # 调用方用 LF 拼 content，文件是 CRLF → 落盘应归一化成 CRLF
        file = tmp_path / "crlf.txt"  # type: ignore[operator]
        file.write_bytes("原始第一行中文内容。\r\n原始第二行中文内容。\r\n".encode("gbk"))
        run(handle_read_file({"path": str(file)}))

        res = run(handle_write_file({
            "path": str(file),
            "content": "新第一行中文内容。\n新第二行中文内容。\n",
        }))
        data = _parse(res[0].text)
        assert data["success"] is True
        after = file.read_bytes()
        assert "新第一行中文内容。\r\n".encode("gbk") in after
        assert "新第一行中文内容。\n".encode("gbk") not in after

    def test_write_crlf_content_to_lf_file_becomes_lf(self, tmp_path: pytest.TempPathFactory) -> None:
        # 反向：文件 LF，content 含 CRLF → 落盘 LF
        file = tmp_path / "lf.txt"  # type: ignore[operator]
        file.write_bytes("原始第一行中文内容。\n原始第二行中文内容。\n".encode("gbk"))
        run(handle_read_file({"path": str(file)}))

        res = run(handle_write_file({
            "path": str(file),
            "content": "新第一行中文内容。\r\n新第二行中文内容。\r\n",
        }))
        data = _parse(res[0].text)
        assert data["success"] is True
        after = file.read_bytes()
        assert "新第一行中文内容。\n".encode("gbk") in after
        assert "新第一行中文内容。\r\n".encode("gbk") not in after

    def test_write_to_mixed_file_not_normalized(self, tmp_path: pytest.TempPathFactory) -> None:
        # 原文件行尾混合 → 不归一化，content 原样落盘
        file = tmp_path / "mix.txt"  # type: ignore[operator]
        file.write_bytes("原始第一行中文。\r\n原始第二行用LF\n".encode("gbk"))
        run(handle_read_file({"path": str(file)}))

        res = run(handle_write_file({
            "path": str(file),
            "content": "新第一行中文内容。\n新第二行中文内容。\n",
        }))
        data = _parse(res[0].text)
        assert data["success"] is True
        after = file.read_bytes()
        # 无单一主流行尾，不归一化，content 的 LF 原样落盘
        assert "新第一行中文内容。\n".encode("gbk") in after
        assert "新第一行中文内容。\r\n".encode("gbk") not in after

    def test_write_new_file_not_normalized(self, tmp_path: pytest.TempPathFactory) -> None:
        # 新文件（不存在）无原行尾可参照 → content 原样落盘
        file = tmp_path / "new.txt"  # type: ignore[operator]
        res = run(handle_write_file({
            "path": str(file), "encoding": "gbk",
            "content": "新第一行中文内容。\n新第二行中文内容。\n",
        }))
        data = _parse(res[0].text)
        assert data["success"] is True
        after = file.read_bytes()
        assert "新第一行中文内容。\n".encode("gbk") in after
        assert "新第一行中文内容。\r\n".encode("gbk") not in after


class TestWriteWideEncodingLineEnding:
    """write 对 UTF-16/32 宽字节编码必须先解码再统计行尾。

    若在原始字节上数换行,0x0A/0x0D 是码元的一部分(CRLF 的 0x0D、0x0A 被 0x00 隔开),
    会被误判成 mixed 而不归一化——本用例断言 LF content 仍被归一化成文件的 CRLF,
    即可捕获“忘记对宽字节编码分流”的回归。
    """

    def test_utf16le_crlf_file_normalizes_lf_content_and_keeps_bom(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        file = tmp_path / "u16.txt"  # type: ignore[operator]
        original = "第一行中文内容。\r\n第二行中文内容。\r\n"
        bom_bytes, _ = converter.encode_from_utf8(original, "utf-16-le")
        file.write_bytes(bom_bytes)
        # 首读缓存编码为 utf-16-le
        read_data = _parse(run(handle_read_file({"path": str(file)}))[0].text)
        assert read_data["encoding"] == "utf-16-le"

        # 用 LF content 覆盖写;文件主流行尾是 CRLF,应归一化成 CRLF,BOM 保留
        res = run(handle_write_file({
            "path": str(file),
            "content": "新第一行中文。\n新第二行中文。\n",
        }))
        assert _parse(res[0].text)["success"] is True

        after = file.read_bytes()
        assert after.startswith(b"\xff\xfe")  # UTF-16-LE BOM 保留
        decoded, _ = converter.decode_to_utf8(after, "utf-16-le")
        assert decoded == "新第一行中文。\r\n新第二行中文。\r\n"

    def test_utf16le_crlf_file_with_undelimited_alias_normalizes_lf(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        # 无分隔符别名 utf16(codecs 合法别名)也要正确判定为宽字节编码,
        # 在原始字节上数换行会得 mixed、content 不被归一化,CRLF 文件被写成 LF。
        file = tmp_path / "u16alias.txt"  # type: ignore[operator]
        original = "第一行中文内容。\r\n第二行中文内容。\r\n"
        bom_bytes, _ = converter.encode_from_utf8(original, "utf-16-le")
        file.write_bytes(bom_bytes)

        res = run(handle_write_file({
            "path": str(file),
            "content": "新第一行中文。\n新第二行中文。\n",
            "encoding": "utf16",
        }))
        assert _parse(res[0].text)["success"] is True

        after = file.read_bytes()
        assert after.startswith(b"\xff\xfe")  # UTF-16-LE BOM 保留
        decoded, _ = converter.decode_to_utf8(after, "utf-16-le")
        assert decoded == "新第一行中文。\r\n新第二行中文。\r\n"


class TestEditFile:
    def test_edit_gbk_file(self, tmp_path: pytest.TempPathFactory) -> None:
        file = tmp_path / "test.txt"  # type: ignore[operator]
        file.write_bytes(_GBK_TEXT.encode("gbk"))
        run(handle_read_file({"path": str(file)}))

        result = run(handle_edit_file({
            "path": str(file),
            "old_string": "测试",
            "new_string": "验证",
        }))
        data = _parse(result[0].text)
        assert data["success"] is True
        assert data["replacements"] == 1

        # 验证替换生效
        detected_enc = data["encoding"]
        content, _ = converter.read_file_as_utf8(file, detected_enc)
        assert "验证" in content
        assert "测试编码" not in content

    def test_edit_not_found(self, tmp_path: pytest.TempPathFactory) -> None:
        file = tmp_path / "test.txt"  # type: ignore[operator]
        file.write_bytes(_GBK_TEXT.encode("gbk"))
        run(handle_read_file({"path": str(file)}))

        result = run(handle_edit_file({
            "path": str(file),
            "old_string": "不存在的内容",
            "new_string": "替换",
        }))
        data = _parse(result[0].text)
        assert data["success"] is False

    def test_edit_multiple_match(self, tmp_path: pytest.TempPathFactory) -> None:
        # 使用足够长的重复文本
        file = tmp_path / "test.txt"  # type: ignore[operator]
        text = "开始部分用于测试，中间用于测试，结尾也用于测试。内容需要足够长。"
        file.write_bytes(text.encode("gbk"))
        run(handle_read_file({"path": str(file)}))

        result = run(handle_edit_file({
            "path": str(file),
            "old_string": "用于测试",
            "new_string": "验证功能",
        }))
        data = _parse(result[0].text)
        assert data["success"] is False
        assert "次" in data["error"]

    def test_edit_replace_all(self, tmp_path: pytest.TempPathFactory) -> None:
        file = tmp_path / "test.txt"  # type: ignore[operator]
        text = "开始部分用于测试，中间用于测试，结尾也用于测试。内容需要足够长。"
        file.write_bytes(text.encode("gbk"))
        run(handle_read_file({"path": str(file)}))

        result = run(handle_edit_file({
            "path": str(file),
            "old_string": "用于测试",
            "new_string": "验证功能",
            "replace_all": True,
        }))
        data = _parse(result[0].text)
        assert data["success"] is True
        assert data["replacements"] == 3

    def test_edit_crlf_lf_mismatch_hint(self, tmp_path: pytest.TempPathFactory) -> None:
        text = "这是第一行中文内容，用于测试。\r\n这是第二行中文内容，继续测试。\r\n这是第三行。"
        file = tmp_path / "crlf.txt"  # type: ignore[operator]
        file.write_bytes(text.encode("gbk"))
        run(handle_read_file({"path": str(file)}))

        # AI 用 LF 拼多行 old_string（文件实际是 CRLF）→ 逐字节匹配失败
        result = run(handle_edit_file({
            "path": str(file),
            "old_string": "这是第一行中文内容，用于测试。\n这是第二行中文内容，继续测试。",
            "new_string": "A\nB",
        }))
        data = _parse(result[0].text)
        assert data["success"] is False
        err = data["error"]
        # 应明确提示行尾不一致
        assert "CRLF" in err and "LF" in err
        # 诊断不改文件
        assert file.read_bytes() == text.encode("gbk")

    def test_edit_genuinely_missing_no_false_lineending_hint(self, tmp_path: pytest.TempPathFactory) -> None:
        text = "这是一行存在的内容，足够长的中文。\r\n这是另一行内容。"
        file = tmp_path / "x.txt"  # type: ignore[operator]
        file.write_bytes(text.encode("gbk"))
        run(handle_read_file({"path": str(file)}))

        result = run(handle_edit_file({
            "path": str(file),
            "old_string": "完全不存在的文本XYZ",
            "new_string": "替换",
        }))
        data = _parse(result[0].text)
        assert data["success"] is False
        # 真正缺失时不应误报为行尾问题
        assert "CRLF" not in data["error"]

    def test_edit_unencodable_new_string_refuses(self, tmp_path: pytest.TempPathFactory) -> None:
        # 不篡改原则：new_string 含目标编码无法表示的字符时，拒绝并报错，不损坏文件
        file = tmp_path / "test.txt"  # type: ignore[operator]
        file.write_bytes("中文旧词内容".encode("gbk"))

        result = run(handle_edit_file({
            "path": str(file), "encoding": "gbk",
            "old_string": "旧词",
            "new_string": "😀",
        }))
        data = _parse(result[0].text)
        assert data["success"] is False
        assert file.read_bytes() == "中文旧词内容".encode("gbk")

    def test_edit_utf8_bom_preserved_when_encoding_param_is_plain_utf8(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        # bug 复现：文件实际带 UTF-8 BOM，但调用方误传 encoding="utf-8"。
        # 写回应以文件实际字节为准保住 BOM（否则丢失 BOM，引发 MSVC C4819），
        # 并在 warnings 中提示编码已被纠正。
        file = tmp_path / "bom.txt"  # type: ignore[operator]
        file.write_bytes(
            b'\xef\xbb\xbf' + "这是一段足够长的中文内容用于测试BOM保留特性。\r\n第二行中文内容继续。\r\n".encode("utf-8")
        )

        result = run(handle_edit_file({
            "path": str(file), "encoding": "utf-8",
            "old_string": "测试", "new_string": "验证",
        }))
        data = _parse(result[0].text)
        assert data["success"] is True
        # BOM 必须保留
        assert file.read_bytes().startswith(b'\xef\xbb\xbf')
        # 应提示编码已纠正为 utf-8-sig
        warnings = data.get("warnings", [])
        assert any("utf-8-sig" in w for w in warnings)
        # 缓存也应是 utf-8-sig，避免后续操作再次丢 BOM
        assert data["encoding"] == "utf-8-sig"


class TestEditMatchLineEndings:
    """match_line_endings 开关：精确匹配失败时，按文件主流行尾归一化 old_string 重试，
    new_string 同步归一化。默认关闭，契约不变。"""

    def test_lf_old_on_crlf_file_succeeds(self, tmp_path: pytest.TempPathFactory) -> None:
        text = "这是第一行中文内容，用于测试。\r\n这是第二行中文内容，继续测试。\r\n这是第三行。"
        file = tmp_path / "crlf.txt"  # type: ignore[operator]
        file.write_bytes(text.encode("gbk"))
        run(handle_read_file({"path": str(file)}))

        # AI 用 LF 拼多行 old_string（文件是 CRLF），开启容错后应成功
        result = run(handle_edit_file({
            "path": str(file),
            "old_string": "这是第一行中文内容，用于测试。\n这是第二行中文内容，继续测试。",
            "new_string": "替换后的第一行\n替换后的第二行",
            "match_line_endings": True,
        }))
        data = _parse(result[0].text)
        assert data["success"] is True
        assert data["replacements"] == 1

        # 落盘内容里 new_string 的 LF 应被转成 CRLF
        content, _ = converter.read_file_as_utf8(file, data["encoding"])
        assert "替换后的第一行\r\n替换后的第二行" in content
        assert "替换后的第一行\n替换后的第二行" not in content

    def test_crlf_old_on_lf_file_succeeds(self, tmp_path: pytest.TempPathFactory) -> None:
        # 反向：LF 文件 + CRLF old_string，容错应转成 LF 匹配
        text = "这是第一行中文内容，用于测试。\n这是第二行中文内容，继续测试。\n"
        file = tmp_path / "lf.txt"  # type: ignore[operator]
        file.write_bytes(text.encode("gbk"))
        run(handle_read_file({"path": str(file)}))

        result = run(handle_edit_file({
            "path": str(file),
            "old_string": "这是第一行中文内容，用于测试。\r\n这是第二行中文内容，继续测试。",
            "new_string": "新行一\r\n新行二",
            "match_line_endings": True,
        }))
        data = _parse(result[0].text)
        assert data["success"] is True
        content, _ = converter.read_file_as_utf8(file, data["encoding"])
        # new_string 的 CRLF 应被转成 LF
        assert "新行一\n新行二" in content
        assert "新行一\r\n新行二" not in content

    def test_genuinely_missing_still_fails(self, tmp_path: pytest.TempPathFactory) -> None:
        # 容错不能凭空制造匹配：old_string 真的不在文件里时仍应失败
        text = "这是一行存在的内容，足够长的中文。\r\n这是另一行内容。\r\n"
        file = tmp_path / "x.txt"  # type: ignore[operator]
        file.write_bytes(text.encode("gbk"))
        run(handle_read_file({"path": str(file)}))

        result = run(handle_edit_file({
            "path": str(file),
            "old_string": "完全不存在的文本XYZ\n第二行也不存在",
            "new_string": "替换",
            "match_line_endings": True,
        }))
        data = _parse(result[0].text)
        assert data["success"] is False
        assert file.read_bytes() == text.encode("gbk")

    def test_mixed_endings_file_not_autofixed(self, tmp_path: pytest.TempPathFactory) -> None:
        # 文件行尾混合（CRLF + LF）时无法确定主流行尾，不应自动归一化
        text = "第一行内容用于测试。\r\n第二行用LF结尾\n第三行"
        file = tmp_path / "mix.txt"  # type: ignore[operator]
        file.write_bytes(text.encode("gbk"))
        run(handle_read_file({"path": str(file)}))

        result = run(handle_edit_file({
            "path": str(file),
            "old_string": "第一行内容用于测试。\n第二行用LF结尾",
            "new_string": "替换",
            "match_line_endings": True,
        }))
        data = _parse(result[0].text)
        assert data["success"] is False
        assert file.read_bytes() == text.encode("gbk")

    def test_exact_match_ignores_flag(self, tmp_path: pytest.TempPathFactory) -> None:
        # old_string 已能精确匹配时，match_line_endings 开关对匹配无影响；
        # 但写回时 new_string 的换行仍按文件主流行尾归一化（CRLF 文件→LF 转 CRLF）
        text = "开头旧词结尾，这是一段足够长的中文内容以确保编码检测稳定可靠。\r\n第二行中文继续。\r\n第三行结尾。"
        file = tmp_path / "f.txt"  # type: ignore[operator]
        file.write_bytes(text.encode("gbk"))
        run(handle_read_file({"path": str(file)}))

        result = run(handle_edit_file({
            "path": str(file),
            "old_string": "旧词",
            "new_string": "新词\n第二行",
            "match_line_endings": True,
        }))
        data = _parse(result[0].text)
        assert data["success"] is True
        # 精确匹配命中，new_string 的 LF 按文件主流行尾转成 CRLF
        content, _ = converter.read_file_as_utf8(file, data["encoding"])
        assert "新词\r\n第二行" in content
        assert "新词\n第二行" not in content

    def test_replace_all_with_normalization(self, tmp_path: pytest.TempPathFactory) -> None:
        text = "标记内容一用于测试。\r\n标记内容二用于测试。\r\n标记内容三用于测试。\r\n"
        file = tmp_path / "f.txt"  # type: ignore[operator]
        file.write_bytes(text.encode("gbk"))
        run(handle_read_file({"path": str(file)}))

        result = run(handle_edit_file({
            "path": str(file),
            "old_string": "用于测试。\n标记内容",
            "new_string": "OK\nNEXT",
            "replace_all": True,
            "match_line_endings": True,
        }))
        data = _parse(result[0].text)
        assert data["success"] is True
        assert data["replacements"] == 2
        content, _ = converter.read_file_as_utf8(file, data["encoding"])
        assert "OK\r\nNEXT" in content


class TestEditMatchIndent:
    """match_indent 开关：精确匹配与行尾容错均失败后，按“逐行去前导空白后的内容 +
    相对缩进层级”整行匹配，容忍深层 tab/空格缩进的计数偏差。命中后写回时用文件实际
    前导空白逐行替换 new_string 的前导空白。默认关闭，契约不变。"""

    def test_deep_tab_off_by_one_succeeds(self, tmp_path: pytest.TempPathFactory) -> None:
        # 文件 6/5 层 tab；模型少写 1 个 tab 给 old/new（5/4 层）→ 容错命中，
        # 写回用文件实际的 6/5 层 tab。多行 under-count 不会被子串精确命中。
        t6, t5, t4 = "\t" * 6, "\t" * 5, "\t" * 4
        file = tmp_path / "deep.txt"  # type: ignore[operator]
        file.write_bytes(
            ("void f() {\n"
             + t6 + "targetCall(arg);\n"
             + t5 + "siblingLine();\n"
             + "}\n").encode("gbk")
        )
        run(handle_read_file({"path": str(file)}))

        result = run(handle_edit_file({
            "path": str(file),
            "old_string": t5 + "targetCall(arg);\n" + t4 + "siblingLine();",
            "new_string": t5 + "targetCall(arg, X);\n" + t4 + "siblingLineRenamed();",
            "match_indent": True,
        }))
        data = _parse(result[0].text)
        assert data["success"] is True
        assert data["replacements"] == 1

        content, _ = converter.read_file_as_utf8(file, data["encoding"])
        # 写回应是文件实际的 6/5 层 tab，而非模型的 5/4 层
        assert (t6 + "targetCall(arg, X);\n") in content
        assert (t5 + "siblingLineRenamed();\n") in content
        # 不应出现 7 层（说明没多加）
        assert ("\t" * 7 + "targetCall") not in content

    def test_tab_style_preserved_against_space_old(self, tmp_path: pytest.TempPathFactory) -> None:
        # 文件用 tab，old/new 用同视觉宽度的空格：命中后写回仍用 tab（保留文件风格）。
        file = tmp_path / "tabs.txt"  # type: ignore[operator]
        file.write_bytes("\tfoo\n\t\tbar\n".encode("gbk"))
        run(handle_read_file({"path": str(file)}))

        # 8 空格 ≈ 1 tab、16 空格 ≈ 2 tab（制表位 8）
        result = run(handle_edit_file({
            "path": str(file),
            "old_string": "        foo\n                bar",
            "new_string": "        NEWFOO\n                NEWBAR",
            "match_indent": True,
        }))
        data = _parse(result[0].text)
        assert data["success"] is True
        content, _ = converter.read_file_as_utf8(file, data["encoding"])
        # 写回用 tab，不是空格
        assert "\tNEWFOO\n\t\tNEWBAR\n" in content
        assert "        NEWFOO" not in content

    def test_ambiguous_refuses(self, tmp_path: pytest.TempPathFactory) -> None:
        # 两处去前导空白后内容与相对缩进均相同（仅绝对深度不同）→ 多义报错，不改文件。
        text = (
            "start\n"
            "\t\tA1\n"        # 2 tab
            "\t\t\tA2\n"      # 3 tab（相对 +1 tab）
            "mid\n"
            "\t\t\t\tA1\n"    # 4 tab
            "\t\t\t\t\tA2\n"  # 5 tab（相对 +1 tab）
            "end\n"
        )
        file = tmp_path / "amb.txt"  # type: ignore[operator]
        file.write_bytes(text.encode("gbk"))
        run(handle_read_file({"path": str(file)}))

        result = run(handle_edit_file({
            "path": str(file),
            "old_string": "\tA1\n\t\tA2",  # 模型给的绝对深度与两处都不精确
            "new_string": "\tB1\n\t\tB2",
            "match_indent": True,
        }))
        data = _parse(result[0].text)
        assert data["success"] is False
        assert "多处" in data["error"]
        # 文件未改动
        assert file.read_bytes() == text.encode("gbk")

    def test_relative_indent_required_swapped_not_matched(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        # 文件 X 比 Y 深；old 给的相对关系反了（X 比 Y 浅）→ 相对缩进不一致，不命中。
        text = "\t\tX\n\tY\n"  # X=2 tab, Y=1 tab
        file = tmp_path / "sw.txt"  # type: ignore[operator]
        file.write_bytes(text.encode("gbk"))
        run(handle_read_file({"path": str(file)}))

        result = run(handle_edit_file({
            "path": str(file),
            "old_string": "\tX\n\t\tY",  # 相对关系与文件相反
            "new_string": "\tXX\n\t\tYY",
            "match_indent": True,
        }))
        data = _parse(result[0].text)
        assert data["success"] is False
        assert file.read_bytes() == text.encode("gbk")

    def test_genuinely_missing_fails(self, tmp_path: pytest.TempPathFactory) -> None:
        text = "\t\tfoo\n\t\tbar\n"
        file = tmp_path / "x.txt"  # type: ignore[operator]
        file.write_bytes(text.encode("gbk"))
        run(handle_read_file({"path": str(file)}))

        result = run(handle_edit_file({
            "path": str(file),
            "old_string": "\t\t不存在的内容XYZ\n\t\t第二行也没有",
            "new_string": "\t\t替换",
            "match_indent": True,
        }))
        data = _parse(result[0].text)
        assert data["success"] is False
        assert file.read_bytes() == text.encode("gbk")

    def test_exact_match_bypasses_indent_path(self, tmp_path: pytest.TempPathFactory) -> None:
        # old_string 已能精确匹配时，即使开启 match_indent 也不做缩进改写：
        # new_string 原样写入（包括模型给的缩进）。
        text = "\t\toldContent\n\tnext\n"
        file = tmp_path / "e.txt"  # type: ignore[operator]
        file.write_bytes(text.encode("gbk"))
        run(handle_read_file({"path": str(file)}))

        result = run(handle_edit_file({
            "path": str(file),
            "old_string": "\t\toldContent",  # 精确匹配
            "new_string": "\tnewContent",     # 故意给不同的缩进，应原样写入
            "match_indent": True,
        }))
        data = _parse(result[0].text)
        assert data["success"] is True
        content, _ = converter.read_file_as_utf8(file, data["encoding"])
        # 精确匹配路径：new_string 原样落盘（缩进未被文件实际值替换）
        assert "\tnewContent\n" in content
        assert "\t\toldContent" not in content

    def test_extra_new_lines_inherit_last_indent(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        # new_string 比 old 多一行：多出的行继承 old 末行的文件实际缩进。
        # old 两行都少算 1 tab（保持相对结构一致）→ 多行 under-count 非子串，走缩进路径。
        t2, t1 = "\t" * 2, "\t" * 1
        file = tmp_path / "extra.txt"  # type: ignore[operator]
        file.write_bytes((t2 + "a();\n" + t2 + "b();\n").encode("gbk"))
        run(handle_read_file({"path": str(file)}))

        result = run(handle_edit_file({
            "path": str(file),
            "old_string": t1 + "a();\n" + t1 + "b();",
            "new_string": t1 + "a();\n" + t1 + "b();\n" + t1 + "c();",  # 末行多出 c()
            "match_indent": True,
        }))
        data = _parse(result[0].text)
        assert data["success"] is True
        content, _ = converter.read_file_as_utf8(file, data["encoding"])
        # 三行都用文件实际的 2 tab
        assert (t2 + "a();\n" + t2 + "b();\n" + t2 + "c();\n") in content

    def test_blank_new_line_stays_blank(self, tmp_path: pytest.TempPathFactory) -> None:
        # new_string 含空行：空行保持为空，不被注入文件缩进导致的行尾空白。
        # 用 over-count（3 tab vs 文件 2 tab）触发缩进路径（under-count 会因子串前缀
        # 精确命中而走不到缩进路径）。
        file = tmp_path / "blank.txt"  # type: ignore[operator]
        file.write_bytes("\t\tfoo();\n".encode("gbk"))
        run(handle_read_file({"path": str(file)}))

        result = run(handle_edit_file({
            "path": str(file),
            "old_string": "\t\t\tfoo();",  # 模型多算 1 tab
            "new_string": "\t\t\tfoo();\n\n\t\t\tbar();",
            "match_indent": True,
        }))
        data = _parse(result[0].text)
        assert data["success"] is True
        content, _ = converter.read_file_as_utf8(file, data["encoding"])
        # 各行都用文件实际的 2 tab；空行无缩进
        lines = content.split("\n")
        # lines: ['<2tab>foo();', '', '<2tab>bar();', '']
        assert lines[0] == "\t\tfoo();"
        assert lines[1] == ""            # 空行无缩进
        assert lines[2] == "\t\tbar();"  # 多出行继承末行(2 tab)

    def test_crlf_file_lf_old_writes_crlf(self, tmp_path: pytest.TempPathFactory) -> None:
        # 文件 CRLF + 深 tab；模型用 LF 且 tab 计数偏差 → 命中，写回用 CRLF 与文件实际 tab，
        # 且被替换行不会因末尾 \r 丢失而降级成 LF。
        file = tmp_path / "crlf.txt"  # type: ignore[operator]
        file.write_bytes(
            "void f() {\r\n"
            "\t\t\t\tdeepCall();\r\n"
            "}\r\n"
            "".encode("gbk")
        )
        run(handle_read_file({"path": str(file)}))

        result = run(handle_edit_file({
            "path": str(file),
            "old_string": "\t\t\t\t\tdeepCall();",  # LF, 多算 1 tab（避免子串精确命中）
            "new_string": "\t\t\t\t\tdeepCall(NEW);",  # LF
            "match_indent": True,
        }))
        data = _parse(result[0].text)
        assert data["success"] is True
        content, _ = converter.read_file_as_utf8(file, data["encoding"])
        # 写回：文件实际的 4 tab + CRLF（被替换行保持 CRLF，未降级为 LF）
        assert "\t\t\t\tdeepCall(NEW);\r\n" in content
        # 整体仍是 3 个 CRLF、无孤立 LF（证明被替换行未降级）
        assert content.count("\r\n") == 3
        assert content.count("\n") == 3

    def test_replace_all_with_indent(self, tmp_path: pytest.TempPathFactory) -> None:
        # 缩进容错的多义保护优先于 replace_all：两处去前导空白后内容与相对缩进均相同
        # （仅绝对深度不同），即便 replace_all=true 也应报错，绝不擅自批量替换。
        text = (
            "start\n"
            + ("\t" * 2) + "dup();\n" + ("\t" * 3) + "inner();\n"
            + "mid\n"
            + ("\t" * 4) + "dup();\n" + ("\t" * 5) + "inner();\n"
            + "end\n"
        )
        file = tmp_path / "ra.txt"  # type: ignore[operator]
        file.write_bytes(text.encode("gbk"))
        run(handle_read_file({"path": str(file)}))

        result = run(handle_edit_file({
            "path": str(file),
            "old_string": "\tdup();\n\t\tinner();",
            "new_string": "\tDUP();\n\t\tINNER();",
            "replace_all": True,
            "match_indent": True,
        }))
        data = _parse(result[0].text)
        assert data["success"] is False
        assert "多处" in data["error"]
        assert file.read_bytes() == text.encode("gbk")

    def test_indent_match_single_unique_occurrence_with_replace_all(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        # 缩进容错唯一命中时 replace_all 无副作用：照常替换一次。
        t3, t2 = "\t" * 3, "\t" * 2
        file = tmp_path / "one.txt"  # type: ignore[operator]
        file.write_bytes((t3 + "onlyHere();\n" + t3 + "next();\n").encode("gbk"))
        run(handle_read_file({"path": str(file)}))

        result = run(handle_edit_file({
            "path": str(file),
            "old_string": t2 + "onlyHere();\n" + t2 + "next();",  # 两行都少算 1 tab
            "new_string": t2 + "onlyHere(NEW);\n" + t2 + "next();",
            "replace_all": True,
            "match_indent": True,
        }))
        data = _parse(result[0].text)
        assert data["success"] is True
        content, _ = converter.read_file_as_utf8(file, data["encoding"])
        assert (t3 + "onlyHere(NEW);\n") in content
        assert "onlyHere();\n" not in content

    def test_off_by_default_no_magic_match(self, tmp_path: pytest.TempPathFactory) -> None:
        # 不开启 match_indent 时，缩进偏差（且非子串精确命中）应直接报错，
        # 保持字节精确匹配契约。用 over-count（3 tab vs 文件 2 tab）确保精确匹配失败。
        file = tmp_path / "d.txt"  # type: ignore[operator]
        file.write_bytes("\t\tfoo();\n".encode("gbk"))
        run(handle_read_file({"path": str(file)}))

        result = run(handle_edit_file({
            "path": str(file),
            "old_string": "\t\t\tfoo();",  # 多 1 tab，精确匹配失败
            "new_string": "\t\t\tbar();",
        }))
        data = _parse(result[0].text)
        assert data["success"] is False
        assert file.read_bytes() == b"\t\tfoo();\n"


class TestGetEncoding:
    def test_after_read(self, tmp_path: pytest.TempPathFactory) -> None:
        file = tmp_path / "test.txt"  # type: ignore[operator]
        file.write_bytes(_GBK_TEXT.encode("gbk"))
        run(handle_read_file({"path": str(file)}))

        result = run(handle_get_encoding({"path": str(file)}))
        data = _parse(result[0].text)
        assert data["success"] is True
        assert data["encoding"] in ("gbk", "gb18030")

    def test_auto_detect_when_no_record(self, tmp_path: pytest.TempPathFactory) -> None:
        # 从未读过该文件，但文件存在 → get_file_encoding 应自动探测并返回编码
        file = tmp_path / "fresh.txt"  # type: ignore[operator]
        file.write_bytes(_GBK_TEXT.encode("gbk"))

        result = run(handle_get_encoding({"path": str(file)}))
        data = _parse(result[0].text)
        assert data["success"] is True
        assert data["encoding"] in ("gbk", "gb18030")

    def test_auto_detect_caches_result(self, tmp_path: pytest.TempPathFactory) -> None:
        # 自动探测的结果应写入缓存，后续 get 直接命中（不再重新探测）
        file = tmp_path / "fresh.txt"  # type: ignore[operator]
        file.write_bytes(_GBK_TEXT.encode("gbk"))

        run(handle_get_encoding({"path": str(file)}))
        from encoding_store import has_encoding
        assert has_encoding(str(file)) is True

    def test_nonexistent_file_errors(self) -> None:
        result = run(handle_get_encoding({"path": "/nonexistent/never_exists.txt"}))
        data = _parse(result[0].text)
        assert data["success"] is False
        assert "文件不存在" in data["error"]


class TestWriteCachesEncoding:
    def test_write_stores_encoding(self, tmp_path: pytest.TempPathFactory) -> None:
        # 用显式编码写入一个从未读过的文件后，应能通过 get_file_encoding 查到编码
        file = tmp_path / "new.txt"  # type: ignore[operator]
        run(handle_write_file({"path": str(file), "content": "测试内容", "encoding": "gbk"}))

        result = run(handle_get_encoding({"path": str(file)}))
        data = _parse(result[0].text)
        assert data["success"] is True
        assert data["encoding"] == "gbk"


class TestEditCachesEncoding:
    def test_edit_stores_encoding(self, tmp_path: pytest.TempPathFactory) -> None:
        # 用显式编码直接 edit 一个从未读过的文件后，应能查到编码
        file = tmp_path / "new.txt"  # type: ignore[operator]
        file.write_bytes("中文旧词内容足够长以供检测".encode("gbk"))

        result = run(handle_edit_file({
            "path": str(file), "encoding": "gbk",
            "old_string": "旧词", "new_string": "新词",
        }))
        assert _parse(result[0].text)["success"] is True

        enc_result = run(handle_get_encoding({"path": str(file)}))
        data = _parse(enc_result[0].text)
        assert data["success"] is True
        assert data["encoding"] == "gbk"


class TestListEncodings:
    def test_empty(self) -> None:
        result = run(handle_list_encodings({}))
        data = _parse(result[0].text)
        assert data["success"] is True
        assert data["count"] == 0

    def test_with_records(self, tmp_path: pytest.TempPathFactory) -> None:
        file = tmp_path / "test.txt"  # type: ignore[operator]
        file.write_bytes(_GBK_TEXT.encode("gbk"))
        run(handle_read_file({"path": str(file)}))

        result = run(handle_list_encodings({}))
        data = _parse(result[0].text)
        assert data["count"] == 1


class TestCallTool:
    def test_unknown_tool(self) -> None:
        result = run(call_tool("unknown_tool", {}))
        data = _parse(result[0].text)
        assert data["success"] is False
        assert "未知工具" in data["error"]


class TestReadCacheFreshness:
    """read 的检测缓存:文件未改动时跳过检测;文件改动后重新检测。"""

    def test_read_skips_detection_when_file_unchanged(
        self, tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        file = tmp_path / "f.txt"  # type: ignore[operator]
        file.write_bytes(_GBK_TEXT.encode("gbk"))
        # 首次读:检测并缓存(含 mtime/size 快照)
        run(handle_read_file({"path": str(file)}))

        import server
        def boom(d: bytes) -> None:
            raise AssertionError("文件未改动,不应再次调用检测")
        # server.py 用 from detector import detect_encoding 绑定了名字,需 patch server 上的引用
        monkeypatch.setattr(server, "detect_encoding", boom)

        result = run(handle_read_file({"path": str(file)}))
        data = _parse(result[0].text)
        assert data["success"] is True
        assert data["encoding"] in ("gbk", "gb18030")

    def test_read_redetects_when_file_changed(
        self, tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        file = tmp_path / "f.txt"  # type: ignore[operator]
        file.write_bytes(_GBK_TEXT.encode("gbk"))
        run(handle_read_file({"path": str(file)}))

        # 改动文件:不同内容,并强制推进 mtime,确保新鲜度判定失效
        import os
        import time
        file.write_bytes("完全不同的新中文内容,足够长以供检测稳定可靠验证。".encode("gbk"))
        future = time.time() + 100
        os.utime(file, (future, future))

        calls: list[int] = []
        import server
        real = server.detect_encoding

        def spy(d: bytes):
            calls.append(len(d))
            return real(d)

        monkeypatch.setattr(server, "detect_encoding", spy)

        result = run(handle_read_file({"path": str(file)}))
        data = _parse(result[0].text)
        assert data["success"] is True
        assert calls, "文件已改动,应重新调用检测"


class TestReadCacheHitSkipsStore:
    """read 缓存命中时不再重写缓存记录:记录未变,重写只是一次无谓的 stat。"""

    def test_cache_hit_does_not_restore(
        self, tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import server
        calls: list = []
        real = server.store_encoding

        def spy(*args, **kwargs):
            calls.append(args)
            return real(*args, **kwargs)

        monkeypatch.setattr(server, "store_encoding", spy)

        file = tmp_path / "f.txt"  # type: ignore[operator]
        file.write_bytes(_GBK_TEXT.encode("gbk"))
        run(handle_read_file({"path": str(file)}))  # 首读:缓存未命中,写一次缓存
        assert len(calls) == 1

        result = run(handle_read_file({"path": str(file)}))  # 命中缓存
        assert _parse(result[0].text)["success"] is True
        assert len(calls) == 1, "缓存命中时不应再写缓存"


class TestReadSelfHeal:
    """read 解码失败自愈:缓存/detect 的 32KB 前缀结论解不开时,按全文重检后
    重试一次,而不是把裸 codec 异常抛给调用方;仍失败才报明确错误。"""

    def test_read_heals_prefix_poisoned_cache(self, tmp_path: pytest.TempPathFactory) -> None:
        # 33KB:前 32KB 全 ASCII,尾部 GBK。detect 判 utf-1 入缓存(只看前缀),
        # read 解码失败后应自愈为 GB 系并返回正确内容
        prefix = b"ascii line\r\n" * 3000
        tail = "尾部中文内容用于制造差异,足够长以便检测稳定。".encode("gbk")
        file = tmp_path / "big.txt"  # type: ignore[operator]
        file.write_bytes(prefix + tail)
        detect_data = _parse(run(handle_detect_file_encoding({"path": str(file)}))[0].text)
        assert detect_data["encoding"] == "utf-8"

        result = run(handle_read_file({"path": str(file)}))
        data = _parse(result[0].text)
        assert data["success"] is True
        assert data["encoding"] in ("gbk", "gb18030")
        assert "尾部中文内容" in data["content"]
        assert any("重新检测" in w for w in data.get("warnings", []))

        # 缓存已被纠正:再次 read 直接命中,无需自愈
        data2 = _parse(run(handle_read_file({"path": str(file)}))[0].text)
        assert data2["success"] is True
        assert data2["encoding"] in ("gbk", "gb18030")
        assert "重新检测" not in "".join(data2.get("warnings", []))

    def test_read_bom_plus_gbk_body(self, tmp_path: pytest.TempPathFactory) -> None:
        # UTF-8 BOM + GBK 正文拼接体(非合法 UTF-8):首次 read 即自愈,
        # 剥 BOM 按 GB 系读取,并明确告知写回时 BOM 会被移除
        body = "这是正文中文内容,用于测试BOM拼接体处理逻辑,足够长。"
        file = tmp_path / "bom_gbk.txt"  # type: ignore[operator]
        file.write_bytes(b"\xef\xbb\xbf" + body.encode("gbk"))

        result = run(handle_read_file({"path": str(file)}))
        data = _parse(result[0].text)
        assert data["success"] is True
        assert data["content"] == body
        assert data["encoding"] in ("gbk", "gb18030")
        assert any("BOM" in w for w in data.get("warnings", []))

    def test_read_undecodable_reports_clear_error(self, tmp_path: pytest.TempPathFactory) -> None:
        # 任何严格编码都解不开的字节(0xFF 对 utf-8/gbk/gb18030/big5 均非法,
        # charset-normalizer 也放弃):应报明确的错误信息,而不是裸 codec 异常
        file = tmp_path / "bin.dat"  # type: ignore[operator]
        file.write_bytes(b"\xff" * 64 + b"\x81" * 64)

        result = run(handle_read_file({"path": str(file)}))
        data = _parse(result[0].text)
        assert data["success"] is False
        assert "无法解码" in data["error"]


class TestEditWriteFreshness:
    """缓存新鲜度(#2):文件被外部工具换编码后,edit/write/get_file_encoding
    重新探测,不拿过期编码解码/编码新字节,避免混合编码损坏。"""

    def _rewrite_as(self, file, text: str, encoding: str) -> None:
        # 模拟外部另存为:换内容并推进 mtime,确保新鲜度判定失效
        import os
        import time
        file.write_bytes(text.encode(encoding))
        future = time.time() + 100
        os.utime(file, (future, future))

    def test_edit_after_external_reencode_to_utf8(self, tmp_path: pytest.TempPathFactory) -> None:
        file = tmp_path / "f.txt"  # type: ignore[operator]
        file.write_bytes(("int f() { return 0; } // 旧中文注释内容".encode("gbk")))
        run(handle_read_file({"path": str(file)}))  # 缓存 GB 系

        self._rewrite_as(file, "int f() { return 0; } // 外部改成UTF-8的内容", "utf-8")

        result = run(handle_edit_file({
            "path": str(file),
            "old_string": "return 0; } // ",
            "new_string": "return 1; } // ",
        }))
        data = _parse(result[0].text)
        assert data["success"] is True
        assert data["encoding"] == "utf-8"
        # 文件仍是合法 UTF-8,无 GBK 字节混入
        assert file.read_bytes() == "int f() { return 1; } // 外部改成UTF-8的内容".encode("utf-8")

    def test_write_after_external_reencode_follows_new_encoding(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        file = tmp_path / "f.txt"  # type: ignore[operator]
        file.write_bytes("旧的中文内容第一行\r\n旧的中文内容第二行\r\n".encode("gbk"))
        run(handle_read_file({"path": str(file)}))

        self._rewrite_as(file, "新的外部内容第一行\r\n新的外部内容第二行\r\n", "utf-8")

        res = run(handle_write_file({
            "path": str(file),
            "content": "写入的新中文内容第一行\r\n写入的新中文内容第二行\r\n",
        }))
        data = _parse(res[0].text)
        assert data["success"] is True
        assert data["encoding"] == "utf-8"
        assert file.read_bytes() == "写入的新中文内容第一行\r\n写入的新中文内容第二行\r\n".encode("utf-8")

    def test_get_encoding_reprobes_after_external_change(self, tmp_path: pytest.TempPathFactory) -> None:
        file = tmp_path / "f.txt"  # type: ignore[operator]
        file.write_bytes(_GBK_TEXT.encode("gbk"))
        run(handle_read_file({"path": str(file)}))

        self._rewrite_as(file, "外部改成UTF-8的中文内容,足够长以供检测稳定可靠。", "utf-8")

        result = run(handle_get_encoding({"path": str(file)}))
        data = _parse(result[0].text)
        assert data["success"] is True
        assert data["encoding"] == "utf-8"

    def test_edit_unchanged_file_skips_reprobe(self, tmp_path: pytest.TempPathFactory,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
        # 文件未改动:edit 直接复用新鲜缓存,不重新探测(每次调用保持低开销)
        file = tmp_path / "f.txt"  # type: ignore[operator]
        file.write_bytes("中文内容足够长以供编码检测稳定可靠。".encode("gbk"))
        run(handle_read_file({"path": str(file)}))

        import detector
        import server
        calls: list = []
        real = detector.detect_file_encoding_details

        def spy(p):
            calls.append(p)
            return real(p)

        monkeypatch.setattr(detector, "detect_file_encoding_details", spy)
        monkeypatch.setattr(server, "detect_file_encoding_details", spy)

        result = run(handle_edit_file({
            "path": str(file), "old_string": "中文", "new_string": "汉字",
        }))
        assert _parse(result[0].text)["success"] is True
        assert calls == [], "文件未改动,不应重新探测"

    def test_edit_with_stale_explicit_encoding_heals(self, tmp_path: pytest.TempPathFactory) -> None:
        # 显式 encoding 优先于缓存且不做新鲜度校验;若它解不开当前字节
        # (调用方拿着过期的编码知识),edit 应自愈重检而不是直接失败。
        # 构造:GBK 文件被外部改成 UTF-8(无 BOM、奇数个汉字→奇数个高位字节,
        # GBK 严格解码必失败),调用方仍显式传 encoding="gbk"。
        file = tmp_path / "f.txt"  # type: ignore[operator]
        file.write_bytes("旧中文内容足够长。".encode("gbk"))
        run(handle_read_file({"path": str(file)}))  # 缓存 GB 系

        new_text = "新中文内容足够长供检测"  # 11 个汉字 = 33 个高位字节(奇数)
        assert len(new_text.encode("utf-8")) % 2 == 1
        file.write_bytes(new_text.encode("utf-8"))

        result = run(handle_edit_file({
            "path": str(file), "encoding": "gbk",
            "old_string": "新中文内容", "new_string": "改后中文内容",
        }))
        data = _parse(result[0].text)
        assert data["success"] is True
        assert data["encoding"] == "utf-8"
        assert file.read_bytes() == "改后中文内容足够长供检测".encode("utf-8")


class TestEncodingAliasNormalization:
    """显式编码名归一化(#5):utf8/utf_8_sig/大写等合法别名归一为规范名,
    不再绕过 UTF-8 BOM 保护与 converter 的 BOM 剥补表。"""

    def test_alias_utf8_write_preserves_bom(self, tmp_path: pytest.TempPathFactory) -> None:
        # 回归复现:传 encoding="utf8" 曾静默丢 BOM 且无警告
        file = tmp_path / "bom.txt"  # type: ignore[operator]
        file.write_bytes(b"\xef\xbb\xbf" + "带BOM的原始UTF-8中文内容,足够长。".encode("utf-8"))

        result = run(handle_write_file({
            "path": str(file), "encoding": "utf8",
            "content": "别名写入的新UTF-8中文内容,足够长。",
        }))
        data = _parse(result[0].text)
        assert data["success"] is True
        assert file.read_bytes().startswith(b"\xef\xbb\xbf")
        assert data["encoding"] == "utf-8-sig"
        assert any("utf-8-sig" in w for w in data.get("warnings", []))

    def test_alias_utf8_edit_preserves_bom(self, tmp_path: pytest.TempPathFactory) -> None:
        file = tmp_path / "bom.txt"  # type: ignore[operator]
        file.write_bytes(b"\xef\xbb\xbf" + "带BOM的原始内容,测试别名编辑,足够长。".encode("utf-8"))

        result = run(handle_edit_file({
            "path": str(file), "encoding": "utf8",
            "old_string": "测试", "new_string": "验证",
        }))
        data = _parse(result[0].text)
        assert data["success"] is True
        assert file.read_bytes().startswith(b"\xef\xbb\xbf")
        assert data["encoding"] == "utf-8-sig"
        assert any("utf-8-sig" in w for w in data.get("warnings", []))

    def test_uppercase_utf8_alias_preserves_bom(self, tmp_path: pytest.TempPathFactory) -> None:
        file = tmp_path / "bom.txt"  # type: ignore[operator]
        file.write_bytes(b"\xef\xbb\xbf" + "带BOM的原始内容,大写别名测试,足够长。".encode("utf-8"))

        result = run(handle_write_file({
            "path": str(file), "encoding": "UTF-8",
            "content": "大写别名写入的新内容,足够长。",
        }))
        data = _parse(result[0].text)
        assert data["success"] is True
        assert file.read_bytes().startswith(b"\xef\xbb\xbf")

    def test_alias_utf_16_le_roundtrip(self, tmp_path: pytest.TempPathFactory) -> None:
        # 下划线别名 utf_16_le 归一为 utf-16-le,写回补对应 BOM
        import codecs as _codecs
        file = tmp_path / "u16.txt"  # type: ignore[operator]
        result = run(handle_write_file({
            "path": str(file), "encoding": "utf_16_le", "content": "中文内容测试别名归一",
        }))
        assert _parse(result[0].text)["success"] is True
        assert file.read_bytes().startswith(_codecs.BOM_UTF16_LE)
        back = run(handle_read_file({"path": str(file)}))
        back_data = _parse(back[0].text)
        assert back_data["success"] is True
        assert back_data["content"] == "中文内容测试别名归一"

    def test_unknown_alias_rejected(self, tmp_path: pytest.TempPathFactory) -> None:
        result = run(handle_write_file({
            "path": str(tmp_path / "f.txt"), "content": "x", "encoding": "not-a-codec",
        }))
        data = _parse(result[0].text)
        assert data["success"] is False
        assert "不支持的编码" in data["error"]

    def test_alias_cp936_normalized_to_gbk(self, tmp_path: pytest.TempPathFactory) -> None:
        # cp936 是 gbk 的别名,归一后缓存与响应里都是规范名
        file = tmp_path / "f.txt"  # type: ignore[operator]
        result = run(handle_write_file({
            "path": str(file), "encoding": "cp936", "content": "中文内容测试规范名",
        }))
        data = _parse(result[0].text)
        assert data["success"] is True
        assert data["encoding"] == "gbk"
        assert file.read_bytes() == "中文内容测试规范名".encode("gbk")

    def test_alias_gb2312_normalized_to_gbk(self, tmp_path: pytest.TempPathFactory) -> None:
        # gb2312 是 gbk 的子集,显式传参与检测侧同口径归一为 gbk
        file = tmp_path / "f.txt"  # type: ignore[operator]
        result = run(handle_write_file({
            "path": str(file), "encoding": "gb2312", "content": "中文内容测试规范名",
        }))
        data = _parse(result[0].text)
        assert data["success"] is True
        assert data["encoding"] == "gbk"
        assert file.read_bytes() == "中文内容测试规范名".encode("gbk")
