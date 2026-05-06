import pytest
from encoding_store import store_encoding, get_encoding, has_encoding, clear_encoding, clear_all, get_all_encodings


@pytest.fixture(autouse=True)
def clean_store() -> None:
    clear_all()


class TestEncodingStore:
    def test_store_and_get(self) -> None:
        store_encoding("/tmp/test.txt", "gbk")
        assert get_encoding("/tmp/test.txt") == "gbk"

    def test_has_encoding(self) -> None:
        assert not has_encoding("/tmp/test.txt")
        store_encoding("/tmp/test.txt", "utf-8")
        assert has_encoding("/tmp/test.txt")

    def test_clear_encoding(self) -> None:
        store_encoding("/tmp/test.txt", "gbk")
        clear_encoding("/tmp/test.txt")
        assert not has_encoding("/tmp/test.txt")

    def test_clear_all(self) -> None:
        store_encoding("/tmp/a.txt", "gbk")
        store_encoding("/tmp/b.txt", "utf-8")
        clear_all()
        assert not has_encoding("/tmp/a.txt")
        assert not has_encoding("/tmp/b.txt")

    def test_get_all_encodings(self) -> None:
        store_encoding("/tmp/a.txt", "gbk")
        store_encoding("/tmp/b.txt", "utf-8")
        result = get_all_encodings()
        assert len(result) == 2

    def test_overwrite(self) -> None:
        store_encoding("/tmp/test.txt", "gbk")
        store_encoding("/tmp/test.txt", "utf-8")
        assert get_encoding("/tmp/test.txt") == "utf-8"

    def test_missing_returns_none(self) -> None:
        assert get_encoding("/tmp/nonexistent.txt") is None
