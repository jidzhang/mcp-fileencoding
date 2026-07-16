"""
字节一致性测试：锁定“不推断、不规范化行尾、往返逐字节一致”契约。

这些测试是契约护栏——任何未来引入行尾规范化（例如把 \\r\\n 统一成 \\n、
或自动给 \\n 补 \\r）的改动，都会被这里的字节级断言挡下。

核心不变量：
- read → 用相同 content write：文件字节与原始逐字节一致。
- edit：仅替换目标子串，其余字节（含所有行尾）逐字节不变。
- CRLF / LF / 孤立 CR 各自原样保留，绝不互相转换。
"""

import asyncio
import json

import pytest

from encoding_store import clear_all
from server import handle_read_file, handle_write_file, handle_edit_file
from converter import decode_to_utf8, encode_from_utf8


@pytest.fixture(autouse=True)
def clean_store() -> None:
    clear_all()


def _parse(text: str) -> dict:
    return json.loads(text)


def run(coro):
    # 与 test_server.py 保持一致：复用 get_event_loop()，避免 asyncio.run()
    # 在 Python 3.13 下把当前事件循环置空，从而破坏其它测试文件的事件循环策略。
    return asyncio.get_event_loop().run_until_complete(coro)


def _read_content(path) -> str:
    """模拟 AI 读文件：返回它实际能看到的 content 字符串。"""
    res = run(handle_read_file({"path": str(path)}))
    data = _parse(res[0].text)
    assert data["success"] is True, data
    return data["content"]


def _round_trip(tmp_path, original: bytes, name: str = "f.txt") -> bytes:
    """写原始字节 → read（检测+存储编码）→ 用读到的 content 原样 write → 返回最终字节。"""
    file = tmp_path / name
    file.write_bytes(original)
    content = _read_content(file)
    res = run(handle_write_file({"path": str(file), "content": content}))
    assert _parse(res[0].text)["success"] is True
    return file.read_bytes()


# ── 整文件往返：字节逐字节一致 ───────────────────────────────────────

class TestFullRoundTripByteIdentity:
    """read → 用相同 content write → 文件字节必须与原始逐字节一致。"""

    @pytest.mark.parametrize("enc,text", [
        ("gbk",     "这是用于字节一致性测试的中文内容第一行，包含足够多汉字以确保检测稳定。\r\n第二行中文内容，同样足够长用于检测。\r\n第三行结尾。"),
        ("gb18030", "这是一段gb18030编码的中文文本，足够长以稳定检测。\r\n第二行内容继续用于测试。\r\n"),
        ("gb18030", "这是带四字节字符的gb18030文件内容，结尾带一个emoji：😀\r\n第二行内容。\r\n"),
        ("utf-8",   "这是一段足够长的UTF-8编码中文文本，用于验证字节往返一致性。\r\n第二行内容继续。\n第三行用LF。"),
    ], ids=["gbk-crlf", "gb18030-crlf", "gb18030-4byte-crlf", "utf8-mixed"])
    def test_round_trip_preserves_bytes(self, tmp_path, enc, text) -> None:
        original = text.encode(enc)
        assert _round_trip(tmp_path, original) == original

    def test_utf8_bom_round_trip(self, tmp_path) -> None:
        original = b'\xef\xbb\xbf' + "带BOM的UTF-8文本，足够长以确保检测。\r\n第二行\n".encode("utf-8")
        assert _round_trip(tmp_path, original) == original


# ── 行尾绝不被规范化 ───────────────────────────────────────────────

class TestLineEndingPreservation:
    """CRLF / LF / 孤立 CR 各自原样保留，绝不互相转换或被丢弃。"""

    def test_crlf_preserved(self, tmp_path) -> None:
        assert _round_trip(tmp_path, b"line1\r\nline2\r\nline3\r\n") == b"line1\r\nline2\r\nline3\r\n"

    def test_lf_preserved(self, tmp_path) -> None:
        assert _round_trip(tmp_path, b"line1\nline2\n") == b"line1\nline2\n"

    def test_lone_cr_preserved(self, tmp_path) -> None:
        # 孤立 CR 绝不被丢弃，也绝不被补成 CRLF
        assert _round_trip(tmp_path, b"a\rb\rc") == b"a\rb\rc"

    def test_mixed_endings_all_preserved(self, tmp_path) -> None:
        original = b"CRLF\r\nLF\nloneCR\rend"
        assert _round_trip(tmp_path, original) == original

    def test_no_trailing_newline_preserved(self, tmp_path) -> None:
        assert _round_trip(tmp_path, b"no newline at end") == b"no newline at end"

    def test_trailing_crlf_preserved(self, tmp_path) -> None:
        assert _round_trip(tmp_path, b"text\r\n") == b"text\r\n"

    def test_nul_and_control_chars_preserved(self, tmp_path) -> None:
        original = b"\x00\x01\x02\x07\x08\x0b\x0cNUL\x00end\r\n"
        assert _round_trip(tmp_path, original) == original


# ── edit：仅替换目标子串，其余字节逐字节不变 ──────────────────────

class TestEditByteImmutability:
    """edit 仅改动被替换子串；其余字节（含所有行尾）逐字节不变。"""

    @staticmethod
    def _expect_after_replace(original: bytes, old_b: bytes, new_b: bytes) -> bytes:
        idx = original.find(old_b)
        assert idx != -1, "old_sub not found in original bytes"
        return original[:idx] + new_b + original[idx + len(old_b):]

    def test_edit_preserves_bytes_outside_replacement(self, tmp_path) -> None:
        text = "这是开头旧词内容，用于测试编辑。\r\n第二行汉字内容继续。\r\n第三行结尾。"
        file = tmp_path / "f.txt"
        original = text.encode("gbk")
        file.write_bytes(original)

        old_sub, new_sub = "旧词", "新词语"
        assert text.count(old_sub) == 1  # 确保唯一，避免多次匹配报错

        res = run(handle_edit_file({
            "path": str(file), "encoding": "gbk",
            "old_string": old_sub, "new_string": new_sub,
        }))
        assert _parse(res[0].text)["success"] is True

        after = file.read_bytes()
        assert after == self._expect_after_replace(original, old_sub.encode("gbk"), new_sub.encode("gbk"))
        # 所有原始 CRLF 仍在
        assert after.count(b"\r\n") == original.count(b"\r\n")

    def test_edit_keeps_mixed_endings_intact(self, tmp_path) -> None:
        text = "这是足够长的中文开头内容用于确保检测稳定可靠，旧词出现在这里。\r\n第二行用LF结尾的中文内容\n第三行用孤立CR结尾\r最后一行"
        file = tmp_path / "f.txt"
        original = text.encode("gbk")
        file.write_bytes(original)

        old_sub, new_sub = "旧词", "替换"
        assert text.count(old_sub) == 1

        res = run(handle_edit_file({
            "path": str(file), "encoding": "gbk",
            "old_string": old_sub, "new_string": new_sub,
        }))
        assert _parse(res[0].text)["success"] is True

        after = file.read_bytes()
        assert after == self._expect_after_replace(original, old_sub.encode("gbk"), new_sub.encode("gbk"))
        # 混合行尾：CRLF / LF / CR 计数都不变
        assert after.count(b"\r\n") == original.count(b"\r\n")
        assert after.count(b"\n") == original.count(b"\n")
        assert after.count(b"\r") == original.count(b"\r")

    def test_edit_newline_only_in_new_string_does_not_touch_rest(self, tmp_path) -> None:
        # old_string 不含换行；new_string 含换行——只影响被替换区，其余字节不变
        text = "前缀内容旧词后缀内容，足够长的中文用于检测稳定。\r\n第二行\r\n第三行"
        file = tmp_path / "f.txt"
        original = text.encode("gbk")
        file.write_bytes(original)

        old_sub, new_sub = "旧词", "新\n词"
        assert text.count(old_sub) == 1

        run(handle_edit_file({
            "path": str(file), "encoding": "gbk",
            "old_string": old_sub, "new_string": new_sub,
        }))
        after = file.read_bytes()
        # 文件是 CRLF，写回时 new_string 的 LF 按主流行尾转成 CRLF
        expected_new = new_sub.replace("\r\n", "\n").replace("\n", "\r\n")
        assert after == self._expect_after_replace(original, old_sub.encode("gbk"), expected_new.encode("gbk"))


# edit 写回归一化：new_string 的换行按文件主流行尾归一化（纯 CRLF/LF；混合不动）
class TestEditNewlineNormalization:
    """edit 写回时把 new_string 的换行归一化成文件主流行尾：纯 CRLF/LF 生效，
    混合行尾 / 孤立 CR / 无换行不动。精确匹配主路径与容错路径行为一致。"""

    def test_crlf_file_new_lf_normalized_to_crlf(self, tmp_path) -> None:
        # 精确匹配命中（old_string 单行），文件 CRLF，new 含 LF → 落盘 CRLF
        text = "开头旧词结尾，足够长的中文内容用于编码检测稳定可靠。\r\n第二行中文继续。\r\n第三行结尾。"
        file = tmp_path / "f.txt"
        file.write_bytes(text.encode("gbk"))
        run(handle_read_file({"path": str(file)}))

        res = run(handle_edit_file({
            "path": str(file), "old_string": "旧词", "new_string": "新\n词",
        }))
        assert _parse(res[0].text)["success"] is True
        after = file.read_bytes()
        assert "新\r\n词".encode("gbk") in after
        assert "新\n词".encode("gbk") not in after
        # 区外原 CRLF 仍在，未被破坏
        assert "第二行中文继续。\r\n".encode("gbk") in after

    def test_lf_file_new_crlf_normalized_to_lf(self, tmp_path) -> None:
        # 反向：文件 LF，new 含 CRLF → 落盘 LF
        text = "开头旧词结尾，足够长的中文内容用于编码检测稳定可靠。\n第二行中文继续。\n第三行结尾。"
        file = tmp_path / "f.txt"
        file.write_bytes(text.encode("gbk"))
        run(handle_read_file({"path": str(file)}))

        res = run(handle_edit_file({
            "path": str(file), "old_string": "旧词", "new_string": "新\r\n词",
        }))
        assert _parse(res[0].text)["success"] is True
        after = file.read_bytes()
        assert "新\n词".encode("gbk") in after
        assert "新\r\n词".encode("gbk") not in after
        assert "第二行中文继续。\n".encode("gbk") in after

    def test_mixed_file_new_not_normalized(self, tmp_path) -> None:
        # 文件行尾混合 → 不归一化，new 原样落盘（守边界，不破坏混合文件）
        text = "开头旧词结尾，足够长的中文内容用于检测稳定。\r\n第二行用LF结尾\n第三行"
        file = tmp_path / "f.txt"
        file.write_bytes(text.encode("gbk"))
        run(handle_read_file({"path": str(file)}))

        res = run(handle_edit_file({
            "path": str(file), "old_string": "旧词", "new_string": "新\n词",
        }))
        assert _parse(res[0].text)["success"] is True
        after = file.read_bytes()
        # 无单一主流行尾，工具不擅自归一化，new 的 LF 原样落盘
        assert "新\n词".encode("gbk") in after
        assert "新\r\n词".encode("gbk") not in after


# ── match_line_endings：容错替换仍只动被替换区，行尾随文件 ─────────

class TestEditMatchLineEndingsByteFidelity:
    """match_line_endings=True 时，替换区外字节逐字节不变；被替换区写入的行尾与文件一致。"""

    def test_crlf_file_lf_old_replaced_with_crlf_new(self, tmp_path) -> None:
        text = "这是足够长的中文开头内容，旧词出现在这里。\r\n第二行中文内容继续。\r\n第三行结尾。"
        file = tmp_path / "f.txt"
        original = text.encode("gbk")
        file.write_bytes(original)

        # old/new 都用 LF，文件是 CRLF；容错后两者都应转成 CRLF
        old_sub = "旧词出现在这里。\r\n第二行中文内容继续。"
        new_sub = "新词出现了。\n全新第二行。"
        # old_sub 已是 CRLF（精确匹配），故意改造成 LF 版本以触发容错
        old_lf = old_sub.replace("\r\n", "\n")
        assert text.count(old_sub) == 1

        res = run(handle_edit_file({
            "path": str(file), "encoding": "gbk",
            "old_string": old_lf, "new_string": new_sub,
            "match_line_endings": True,
        }))
        assert _parse(res[0].text)["success"] is True

        after = file.read_bytes()
        # 期望：把 old_sub(CRLF) 替换成 new_sub 的 CRLF 版本
        expected_new = new_sub.replace("\r\n", "\n").replace("\n", "\r\n")
        idx = original.find(old_sub.encode("gbk"))
        expected = original[:idx] + expected_new.encode("gbk") + original[idx + len(old_sub.encode("gbk")):]
        assert after == expected
        # 替换区外 CRLF 计数不变（替换区内也用了 CRLF）
        assert after.count(b"\r\n") == original.count(b"\r\n")
        # new_sub 的 LF 不应原样落盘
        assert new_sub.encode("gbk") not in after


# ── match_indent：缩进容错仍只动被替换区，缩进/行尾随文件 ─────────

class TestEditMatchIndentByteFidelity:
    """match_indent=True 时，替换区外字节逐字节不变；被替换区写入的缩进（tab 数）
    与行尾（CRLF）都与文件一致，不被 new_string 里的错误值污染。"""

    def test_indent_tolerant_replace_preserves_outside_bytes(self, tmp_path) -> None:
        # 用显式乘法构造 tab，避免字面量数 tab 出错。
        t4 = "\t" * 4
        t3 = "\t" * 3
        t2 = "\t" * 2
        text = (
            "这是开头足够长的中文内容用于检测稳定。\r\n"
            + t4 + "oldFunc(arg);\r\n"     # 文件：4 tab
            + t3 + "nextLine();\r\n"       # 文件：3 tab
            + "这是结尾中文内容。\r\n"
        )
        file = tmp_path / "f.txt"
        original = text.encode("gbk")
        file.write_bytes(original)

        # 模型两行都少算 1 个 tab（3/2 而非 4/3），相对缩进结构一致；用 LF。
        # 多行 under-count 不会被子串精确命中，故走到缩进容错路径。
        old_sub = t3 + "oldFunc(arg);\n" + t2 + "nextLine();"
        new_sub = t3 + "oldFunc(NEW);\n" + t2 + "newLine();"

        res = run(handle_edit_file({
            "path": str(file), "encoding": "gbk",
            "old_string": old_sub, "new_string": new_sub,
            "match_indent": True,
        }))
        assert _parse(res[0].text)["success"] is True

        after = file.read_bytes()
        # 期望：替换区写成文件实际的 4/3 tab + CRLF；区外字节原样
        expected_text = (
            "这是开头足够长的中文内容用于检测稳定。\r\n"
            + t4 + "oldFunc(NEW);\r\n"
            + t3 + "newLine();\r\n"
            + "这是结尾中文内容。\r\n"
        )
        assert after == expected_text.encode("gbk")
        # 行尾全部仍是 CRLF，计数不变
        assert after.count(b"\r\n") == original.count(b"\r\n")
        assert after.count(b"\n") == original.count(b"\n")
        # 模型给的 LF 版本不应原样落盘
        assert new_sub.encode("gbk") not in after


# ── converter 层：decode→encode 逐字节一致（无 BOM 时） ───────────

class TestConverterByteIdentity:
    """converter 层的 decode→encode 必须逐字节一致（防御性独立验证）。"""

    def test_gbk_decode_encode_identity(self) -> None:
        original = "中文\r\n混合\n行尾\r测试内容".encode("gbk")
        text, _ = decode_to_utf8(original, "gbk")
        out, _ = encode_from_utf8(text, "gbk")
        assert out == original

    def test_gb18030_four_byte_identity(self) -> None:
        original = "中文\r\n内容😀测试".encode("gb18030")
        text, _ = decode_to_utf8(original, "gb18030")
        out, _ = encode_from_utf8(text, "gb18030")
        assert out == original

    def test_utf8_decode_encode_identity(self) -> None:
        original = "中文\r\n混合\n测试".encode("utf-8")
        text, _ = decode_to_utf8(original, "utf-8")
        out, _ = encode_from_utf8(text, "utf-8")
        assert out == original


# ── UTF-16/32：BOM 与字节序逐字节一致 ──────────────────────────────

class TestUtf16Utf32RoundTrip:
    """UTF-16/32 文件经 read → write 必须逐字节一致（含 BOM、字节序、所有行尾）。"""

    @pytest.mark.parametrize("enc,bom", [
        ("utf-16-le", b'\xff\xfe'),
        ("utf-16-be", b'\xfe\xff'),
        ("utf-32-le", b'\xff\xfe\x00\x00'),
        ("utf-32-be", b'\x00\x00\xfe\xff'),
    ], ids=["u16le", "u16be", "u32le", "u32be"])
    def test_round_trip_preserves_bytes(self, tmp_path, enc: str, bom: bytes) -> None:
        original = bom + "中文内容测试，含CRLF\r\n和LF\n的文本，足够长。".encode(enc)
        assert _round_trip(tmp_path, original) == original

    def test_read_content_has_no_bom_char(self, tmp_path) -> None:
        # AI 看到的 content 不应含前导（BOM 已在读时去除）
        original = b'\xff\xfe' + "中文内容测试\r\n第二行内容。".encode("utf-16-le")
        file = tmp_path / "u16.txt"
        file.write_bytes(original)
        content = _read_content(file)
        assert not content.startswith("﻿")
        assert "中文内容测试" in content
