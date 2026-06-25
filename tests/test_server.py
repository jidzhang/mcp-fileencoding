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
        # old_string 已能精确匹配时，即使开启容错也不做行尾转换
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
        # 精确匹配命中，new_string 的 LF 原样写入，不被转成 CRLF
        content, _ = converter.read_file_as_utf8(file, data["encoding"])
        assert "新词\n第二行" in content

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
