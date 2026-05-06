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


class TestGetEncoding:
    def test_after_read(self, tmp_path: pytest.TempPathFactory) -> None:
        file = tmp_path / "test.txt"  # type: ignore[operator]
        file.write_bytes(_GBK_TEXT.encode("gbk"))
        run(handle_read_file({"path": str(file)}))

        result = run(handle_get_encoding({"path": str(file)}))
        data = _parse(result[0].text)
        assert data["success"] is True
        assert data["encoding"] in ("gbk", "gb18030")

    def test_no_record(self, tmp_path: pytest.TempPathFactory) -> None:
        result = run(handle_get_encoding({"path": "/tmp/unknown.txt"}))
        data = _parse(result[0].text)
        assert data["success"] is False


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
