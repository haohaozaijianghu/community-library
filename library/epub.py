from html.parser import HTMLParser
from posixpath import dirname, join, normpath
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from library.dependencies import split_piece
from library.dependencies import LibraryError

LIMIT = 40 * 1024 * 1024


class ChapterParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag in {"head", "script", "style"}:
            self.hidden += 1
        if tag in {"p", "br", "li", "h1", "h2", "h3", "div"}:
            self.text.append("\n")

    def handle_endtag(self, tag):
        if tag in {"head", "script", "style"} and self.hidden:
            self.hidden -= 1
        if tag in {"p", "li", "h1", "h2", "h3", "div"}:
            self.text.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.text.append(data)


def epub_blocks(path):
    try:
        with ZipFile(path) as file:
            entries = file.infolist()
            if len(entries) > 4000 or sum(entry.file_size for entry in entries) > LIMIT:
                raise LibraryError(422, "document_limit", "EPUB 解压后超过处理上限")
            container = ElementTree.fromstring(file.read("META-INF/container.xml"))
            package_path = container.find(".//{*}rootfile").attrib["full-path"]
            package = ElementTree.fromstring(file.read(package_path))
            manifest = {item.attrib["id"]: item for item in package.findall("./{*}manifest/{*}item")}
            for chapter, ref in enumerate(package.findall("./{*}spine/{*}itemref"), 1):
                item = manifest[ref.attrib["idref"]]
                if item.get("media-type") not in {"application/xhtml+xml", "text/html"}:
                    continue
                href = urlsplit(item.attrib["href"])
                if href.scheme or href.netloc:
                    continue
                chapter_path = normpath(join(dirname(package_path), unquote(href.path)))
                parser = ChapterParser()
                parser.feed(file.read(chapter_path).decode("utf-8-sig"))
                parser.close()
                for line in "".join(parser.text).splitlines():
                    if line.strip():
                        yield from split_piece(line.strip(), f"第 {chapter} 章")
    except (BadZipFile, ElementTree.ParseError, KeyError, AttributeError, UnicodeError, RuntimeError) as exc:
        raise LibraryError(422, "unreadable_document", "EPUB 文件无法解析") from exc
