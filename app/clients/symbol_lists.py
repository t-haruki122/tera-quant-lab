"""Fetch and validate the JPX and Nasdaq Trader lists."""

import re
import xml.etree.ElementTree as ET
import zipfile
import zlib
from html import unescape
from io import BytesIO, StringIO
from urllib.parse import urljoin

import httpx

from app.schemas.symbols import SymbolMatch

JPX_PAGE = "https://www.jpx.co.jp/markets/statistics-equities/misc/01.html"
JPX_XLSX = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xlsx"
NASDAQ_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024
MAX_MEMBER_BYTES = 16 * 1024 * 1024
MAX_ROWS = 50_000
JPX_LINK = re.compile(
    r'''\bhref\s*=\s*["']((?:(?:https://www\.jpx\.co\.jp)?/markets/statistics-equities/misc/|\./)?'''
    r'''(?:[\w-]+/)*data_j\.xlsx?)(?:[?#][^"']*)?["']''',
    re.IGNORECASE | re.ASCII,
)
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
JPX_HEADERS = (
    "日付", "コード", "銘柄名", "市場・商品区分", "33業種コード", "33業種区分",
    "17業種コード", "17業種区分", "規模コード", "規模区分",
)
US_EXCHANGES = {
    "A": "NYSE American", "N": "NYSE", "P": "NYSE Arca", "Z": "Cboe BZX", "V": "IEX",
    "F": "Texas Stock Exchange", "M": "NYSE Texas",
}
US_NAME_SUFFIX = re.compile(r"\s+(?:-\s*)?(?:(Class\s+[A-Z0-9]+)\s+)?Common Stock\s*$", re.IGNORECASE)
US_SPECIAL_ISSUE = re.compile(
    r"\b(?:warrants?|rights?)\s*$|\s-\s+(?:(?:Class [A-Z0-9]+|Ordinary)\s+)?Units?\s*$"
    r"|\b(?:units?\s*,?\s+(?:each|consisting)|acquisition\b.*\bunits?\s*$)",
    re.IGNORECASE,
)


class SymbolListError(ValueError):
    """A provider list exceeds a limit or has an unexpected format."""


def discover_jpx_link(page: str) -> str | None:
    links = JPX_LINK.findall(unescape(page))
    path = next((link for link in links if link.lower().endswith(".xlsx")), links[0] if links else None)
    return urljoin(JPX_PAGE, path) if path else None


async def download(client: httpx.AsyncClient, url: str) -> bytes:
    async with client.stream("GET", url) as response:
        response.raise_for_status()
        if response.headers.get("Content-Encoding", "identity").lower() != "identity":
            raise SymbolListError("銘柄一覧の HTTP 圧縮形式が想定と異なります。")
        length = response.headers.get("Content-Length", "")
        if length.isdecimal() and int(length) > MAX_DOWNLOAD_BYTES:
            raise SymbolListError("銘柄一覧がダウンロードサイズの上限を超えています。")
        body = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
            if len(body) + len(chunk) > MAX_DOWNLOAD_BYTES:
                raise SymbolListError("銘柄一覧がダウンロードサイズの上限を超えています。")
            body.extend(chunk)
        return bytes(body)


def _xml(book: zipfile.ZipFile, member: str, root_tag: str) -> ET.Element:
    with book.open(member) as stream:
        data = stream.read(MAX_MEMBER_BYTES + 1)
    if len(data) > MAX_MEMBER_BYTES:
        raise SymbolListError("JPX ファイルの展開サイズが上限を超えています。")
    # Accept UTF-8 OOXML only and disallow DTDs before invoking ElementTree.
    xml = data.decode("utf-8-sig")
    if "\0" in xml or "<!DOCTYPE" in xml or "<!ENTITY" in xml:
        raise SymbolListError("JPX ファイルに未対応の XML 宣言が含まれています。")
    root = ET.fromstring(xml)
    if root.tag != NS + root_tag:
        raise SymbolListError(f"JPX ファイルに想定した XML がありません（{member}）。")
    return root


def _string(element: ET.Element) -> str:
    nodes = element.findall(f"{NS}t") + element.findall(f"{NS}r/{NS}t")
    return "".join(node.text or "" for node in nodes).strip()


def _sheet_row(row: ET.Element, strings: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for cell in row.findall(f"{NS}c"):
        ref = re.fullmatch(r"([A-Z]+)[1-9][0-9]*", cell.get("r", ""))
        if ref is None or ref[1] in values:
            raise SymbolListError("JPX ファイルのセル参照が不正です。")
        cell_type = cell.get("t", "n")
        raw = (cell.findtext(f"{NS}v") or "").strip()
        if cell_type == "s":
            if not raw.isascii() or not raw.isdecimal() or int(raw) >= len(strings):
                raise SymbolListError("JPX ファイルの共有文字列参照が不正です。")
            raw = strings[int(raw)]
        elif cell_type == "inlineStr":
            inline = cell.find(f"{NS}is")
            raw = _string(inline) if inline is not None else ""
        elif cell_type not in {"n", "str"}:
            raise SymbolListError("JPX ファイルに未対応のセル形式があります。")
        values[ref[1]] = raw
    return values


def parse_jpx(data: bytes) -> list[SymbolMatch]:
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise SymbolListError("JPX ファイルがダウンロードサイズの上限を超えています。")
    try:
        with zipfile.ZipFile(BytesIO(data)) as book:
            members = book.infolist()
            names = set(book.namelist())
            if len(members) > 128 or len(members) != len(names):
                raise SymbolListError("JPX ファイルの ZIP 構成が不正です。")
            if any(member.file_size > MAX_MEMBER_BYTES for member in members):
                raise SymbolListError("JPX ファイルの展開サイズが上限を超えています。")
            strings = []
            if "xl/sharedStrings.xml" in names:
                shared = _xml(book, "xl/sharedStrings.xml", "sst")
                strings = [_string(node) for node in shared.findall(f"{NS}si")]
            sheet = _xml(book, "xl/worksheets/sheet1.xml", "worksheet")
        rows = sheet.findall(f"{NS}sheetData/{NS}row")
        if not 2 <= len(rows) <= MAX_ROWS + 1:
            raise SymbolListError("JPX ファイルの行数が想定範囲外です。")
        header = _sheet_row(rows[0], strings)
        if tuple(header.get(column) for column in "ABCDEFGHIJ") != JPX_HEADERS:
            raise SymbolListError("JPX ファイルの列見出しが想定と異なります。")
        result: list[SymbolMatch] = []
        seen: set[str] = set()
        for row in rows[1:]:
            values = _sheet_row(row, strings)
            if not any(values.values()):
                continue
            code, name, market, sector = (values.get(column, "") for column in "BCDF")
            if not re.fullmatch(r"[0-9A-Z]{4,5}", code) or not name or name == "-" or code in seen:
                raise SymbolListError(f"JPX ファイルの銘柄行が不正です（行 {row.get('r', '?')}）。")
            seen.add(code)
            segment = market.split("（", 1)[0] if market and market != "-" else None
            result.append(SymbolMatch(
                f"{code}.T", name, "JP", segment, sector if sector and sector != "-" else None,
                bool(segment and "ETF" in segment), "jpx",
            ))
    except SymbolListError:
        raise
    except (zipfile.BadZipFile, ET.ParseError, KeyError, ValueError, RuntimeError, EOFError, zlib.error) as exc:
        raise SymbolListError("JPX ファイルを .xlsx として読み取れません。") from exc
    if not result:
        raise SymbolListError("JPX ファイルに銘柄がありません。")
    return result


def parse_nasdaq(data: bytes, *, other: bool = False) -> list[SymbolMatch]:
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise SymbolListError("米国銘柄一覧がダウンロードサイズの上限を超えています。")
    try:
        rows = (line.rstrip("\r\n").split("|") for line in StringIO(data.decode("utf-8-sig")))
    except UnicodeError as exc:
        raise SymbolListError("米国銘柄一覧を UTF-8 として読み取れません。") from exc
    symbol_column = "ACT Symbol" if other else "Symbol"
    required = {symbol_column, "Security Name", "Test Issue", "ETF"}
    if other:
        required.add("Exchange")
    header = next(rows, [])
    if not required.issubset(header) or len(set(header)) != len(header):
        raise SymbolListError("米国銘柄一覧の列見出しが想定と異なります。")
    result: dict[str, SymbolMatch] = {}
    footer = False
    for number, row in enumerate(rows, 2):
        if number > MAX_ROWS + 2:
            raise SymbolListError("米国銘柄一覧の行数が上限を超えています。")
        if not row or not any(row):
            continue
        if footer:
            raise SymbolListError("米国銘柄一覧の末尾が不正です。")
        if row[0].startswith("File Creation Time:"):
            footer = True
            continue
        if len(row) != len(header):
            raise SymbolListError(f"米国銘柄一覧の列数が不正です（行 {number}）。")
        fields = dict(zip(header, row, strict=True))
        symbol, name = fields[symbol_column].strip(), fields["Security Name"].strip()
        if fields["Test Issue"] == "Y" or "$" in symbol:
            continue
        is_etf = fields["ETF"] == "Y"
        if not is_etf and US_SPECIAL_ISSUE.search(name):
            continue
        if not re.fullmatch(r"[A-Z0-9]+(?:[.-][A-Z0-9]+)*", symbol) or len(symbol) > 20:
            continue
        if not name or fields["ETF"] not in {"Y", "N"} or fields["Test Issue"] != "N":
            raise SymbolListError(f"米国銘柄一覧の銘柄行が不正です（行 {number}）。")
        symbol = symbol.replace(".", "-")
        suffix = US_NAME_SUFFIX.search(name)
        if suffix:
            label = name[:suffix.start()].rstrip(" ,-")
            if label:
                name = f"{label} ({suffix[1]})" if suffix[1] else label
        segment = US_EXCHANGES.get(fields["Exchange"]) if other else "NASDAQ"
        result.setdefault(symbol, SymbolMatch(symbol, name, "US", segment, None, is_etf, "nasdaqtrader"))
    if not result or not footer:
        raise SymbolListError("米国銘柄一覧に銘柄または作成日時がありません。")
    return list(result.values())
