import pytest

from library.agent import availability_title, category_requested
from library.books import catalog_requested


@pytest.mark.parametrize("query", [
    "现在有哪些书",
    "都有哪些书籍",
    "北馆目前有哪些图书？",
    "请问现在本馆都有什么书呢？",
    "查看本馆馆藏目录",
])
def test_general_catalog_phrases(query):
    assert catalog_requested(query, "north")


@pytest.mark.parametrize("query", [
    "本馆有哪些电子书？",
    "目前南馆都有哪些已发布电子书？",
    "请问馆里可以看哪些电子图书？",
])
def test_ebook_catalog_phrases(query):
    assert catalog_requested(query, "south", include_ebooks=True)
    assert not catalog_requested(query, "south")


@pytest.mark.parametrize("query", [
    "南馆现在有哪些书？",
    "现在南馆有哪些电子书？",
    "现在有哪些书讲香樟？",
    "《街巷里的树》里有哪些树？",
])
def test_non_catalog_questions(query):
    assert not catalog_requested(query, "north", include_ebooks=True)


@pytest.mark.parametrize("query", [
    "有街巷里的树 这本书么",
    "有没有街巷里的树？",
    "请问本馆有《街巷里的树》吗？",
])
def test_book_availability_preserves_title(query):
    assert availability_title(query, "north") == "街巷里的树"


@pytest.mark.parametrize("query", [
    "南馆有《街巷里的树》吗？",
    "有没有关于香樟的书？",
    "有《街巷里的树》这本书讲了什么？",
    "有没有街巷里的树这本书讲什么？",
])
def test_title_availability_does_not_swallow_other_questions(query):
    assert availability_title(query, "north") is None


@pytest.mark.parametrize("query", ["都有哪些种类的书籍", "电子书有哪几类", "有哪些图书分类", "北馆目前都有哪些书籍类型？"])
def test_category_queries(query):
    assert category_requested(query, "north")
    assert not category_requested("南馆现在有哪些书籍类型？", "north")
    assert not category_requested("《街巷里的树》有哪些树的种类？", "north")
