"""Local database and upload helpers for the standalone library."""
import hashlib
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

from docx import Document
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pypdf import PdfReader

TYPES = {".pdf", ".docx", ".md", ".markdown", ".txt"}
MAX_BLOCKS = 4000


class LibraryError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code


def database(connection_string):
    return ConnectionPool(
        connection_string, kwargs={"row_factory": dict_row}, min_size=1, max_size=8, open=False,
    )


@dataclass(frozen=True)
class Passage:
    text: str
    locator: str


def split_piece(text, locator):
    remaining = text.strip()
    while remaining:
        end = len(remaining)
        if end > 1200:
            end = max(remaining.rfind(" ", 800, 1200), remaining.rfind("。", 800, 1200)) + 1 or 1200
        yield Passage(remaining[:end].strip(), locator)
        remaining = remaining[end:].lstrip()


def raw_passages(path):
    extension = path.suffix
    if extension == ".pdf":
        for number, page in enumerate(PdfReader(path).pages, 1):
            yield page.extract_text() or "", f"第 {number} 页"
    elif extension == ".docx":
        with ZipFile(path) as archive:
            if sum(entry.file_size for entry in archive.infolist()) > 40 * 1024 * 1024:
                raise LibraryError(422, "document_limit", "DOCX 解压后超过处理上限")
        for number, paragraph in enumerate(Document(path).paragraphs, 1):
            yield paragraph.text, f"第 {number} 段"
    else:
        with path.open(encoding="utf-8-sig") as content:
            for number, text in enumerate(content, 1):
                yield text, f"第 {number} 行"


def read_blocks(path):
    passages = []
    try:
        for text, locator in raw_passages(path):
            for passage in split_piece(text, locator):
                passages.append(passage)
                if len(passages) > MAX_BLOCKS:
                    raise LibraryError(422, "document_limit", "原文片段超过处理上限")
    except LibraryError:
        raise
    except Exception as error:
        raise LibraryError(422, "unreadable_document", "上传文件无法解析") from error
    return passages


class Files:
    def __init__(self, root):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def path(self, key):
        return self.root / key

    def write(self, filename, stream, limit, *, allowed_types=TYPES):
        name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
        extension = Path(name).suffix.lower()
        if size_invalid(name) or extension not in allowed_types:
            raise LibraryError(415, "unsupported_file", "不支持此文件类型")
        key = uuid4().hex + extension
        part = self.path(key + ".part")
        checksum = hashlib.sha256()
        total = 0
        try:
            with part.open("xb") as target:
                for chunk in iter(lambda: stream.read(64 * 1024), b""):
                    total += len(chunk)
                    if total > limit:
                        raise LibraryError(413, "file_too_large", "上传文件超过大小限制")
                    checksum.update(chunk)
                    target.write(chunk)
            if not total:
                raise LibraryError(422, "empty_file", "文件内容为空")
            part.replace(self.path(key))
        finally:
            part.unlink(missing_ok=True)
        return name, key, total, checksum.hexdigest()


def size_invalid(filename):
    return not filename or len(filename) > 240 or any(ord(char) < 32 for char in filename)
