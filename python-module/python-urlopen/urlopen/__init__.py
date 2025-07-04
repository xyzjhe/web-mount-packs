#!/usr/bin/env python3
# coding: utf-8

__author__ = "ChenyangGao <https://chenyanggao.github.io>"
__version__ = (0, 0, 8)
__all__ = ["urlopen", "request", "download"]

import errno

from collections.abc import Callable, Generator, Iterable, Mapping, Sequence
from copy import copy
from gzip import decompress as decompress_gzip
from http.client import HTTPResponse
from http.cookiejar import CookieJar
from inspect import isgenerator
from os import fsdecode, fstat, makedirs, PathLike
from os.path import abspath, dirname, isdir, join as joinpath
from re import compile as re_compile
from shutil import COPY_BUFSIZE # type: ignore
from socket import getdefaulttimeout, setdefaulttimeout
from ssl import SSLContext, _create_unverified_context
from string import punctuation
from types import EllipsisType
from typing import cast, Any, Literal
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import build_opener, HTTPCookieProcessor, HTTPSHandler, OpenerDirector, Request
from zlib import compressobj, DEF_MEM_LEVEL, DEFLATED, MAX_WBITS

from argtools import argcount
from filewrap import bio_skip_iter, SupportsWrite
from http_response import get_filename, get_length, is_chunked, is_range_request
from orjson import dumps, loads


if "__del__" not in HTTPResponse.__dict__:
    setattr(HTTPResponse, "__del__", HTTPResponse.close)
if "__del__" not in OpenerDirector.__dict__:
    setattr(OpenerDirector, "__del__", OpenerDirector.close)

_opener: None | OpenerDirector = None
CRE_search_charset = re_compile(r"\bcharset=(?P<charset>[^ ;]+)").search

if getdefaulttimeout() is None:
    setdefaulttimeout(60)


def decompress_deflate(data: bytes, compresslevel: int = 9) -> bytes:
    # Fork from: https://stackoverflow.com/questions/1089662/python-inflate-and-deflate-implementations#answer-1089787
    compress = compressobj(
            compresslevel,  # level: 0-9
            DEFLATED,       # method: must be DEFLATED
            -MAX_WBITS,     # window size in bits:
                            #   -15..-8: negate, suppress header
                            #   8..15: normal
                            #   16..30: subtract 16, gzip header
            DEF_MEM_LEVEL,  # mem level: 1..8/9
            0               # strategy:
                            #   0 = Z_DEFAULT_STRATEGY
                            #   1 = Z_FILTERED
                            #   2 = Z_HUFFMAN_ONLY
                            #   3 = Z_RLE
                            #   4 = Z_FIXED
    )
    deflated = compress.compress(data)
    deflated += compress.flush()
    return deflated


def get_charset(content_type: str, default="utf-8") -> str:
    match = CRE_search_charset(content_type)
    if match is None:
        return "utf-8"
    return match["charset"]


def ensure_ascii_url(url: str, /) -> str:
    if url.isascii():
        return url
    return quote(url, safe=punctuation)


def decompress_response(resp: HTTPResponse, /) -> bytes:
    data = resp.read()
    content_encoding = resp.headers.get("Content-Encoding")
    match content_encoding:
        case "gzip":
            data = decompress_gzip(data)
        case "deflate":
            data = decompress_deflate(data)
        case "br":
            from brotli import decompress as decompress_br # type: ignore
            data = decompress_br(data)
        case "zstd":
            from zstandard import decompress as decompress_zstd
            data = decompress_zstd(data)
    return data


def urlopen(
    url: str | Request, 
    method: str = "GET", 
    params: None | str | Mapping | Sequence[tuple[Any, Any]] = None, 
    data: None | bytes | str | Mapping | Sequence[tuple[Any, Any]] | Iterable[bytes] = None, 
    json: Any = None, 
    headers: None | Mapping[str, str] = None, 
    timeout: None | int | float = None, 
    cookies: None | CookieJar = None, 
    proxy: None | tuple[str, str] = None, 
    context: None | SSLContext = None, 
    opener: None | OpenerDirector = None, 
    origin: None | str = None, 
) -> HTTPResponse:
    global _opener
    if isinstance(url, str) and not urlsplit(url).scheme:
        if origin:
            if not url.startswith("/"):
                url = "/" + url
            url = origin + url
    if params:
        if not isinstance(params, str):
            params = urlencode(params)
    params = cast(None | str, params)
    if json is not None:
        if isinstance(json, bytes):
            data = json
        else:
            data = dumps(json)
        if headers:
            headers = {**headers, "Content-type": "application/json; charset=UTF-8"}
        else:
            headers = {"Content-type": "application/json; charset=UTF-8"}
    elif data is not None:
        if isinstance(data, bytes):
            pass
        elif isinstance(data, str):
            data = data.encode("utf-8")
        elif isinstance(data, (Mapping, Sequence)):
            data = urlencode(cast(Mapping | Sequence, data)).encode("latin-1")
            if headers:
                headers = {**headers, "Content-type": "application/x-www-form-urlencoded"}
            else:
                headers = {"Content-type": "application/x-www-form-urlencoded"}
    data = cast(None | bytes | Iterable[bytes], data)
    if isinstance(url, Request):
        req = url
        if params:
            req.full_url += "?&"["?" in req.full_url] + params
        if headers:
            for key, val in headers.items():
                req.add_header(key, val)
        if data is not None:
            req.data = data
        req.method = method.upper()
    else:
        if headers:
            headers = dict(headers)
        else:
            headers = {}
        if params:
            url += "?&"["?" in url] + params
        req = Request(url, data=data, headers=headers, method=method.upper())
    if proxy:
        req.set_proxy(*proxy)
    if opener is None:
        if _opener is None:
            opener = _opener = build_opener(HTTPSHandler(context=_create_unverified_context()))
        else:
            opener = _opener
    if context is not None or cookies is not None:
        opener = copy(opener)
        if context is not None:
            opener.add_handler(HTTPSHandler(context=context))
        if cookies is not None:
            opener.add_handler(HTTPCookieProcessor(cookies))
    req.full_url = ensure_ascii_url(req.full_url)
    if timeout is None:
        return opener.open(req)
    else:
        return opener.open(req, timeout=timeout)


def request(
    url: str | Request, 
    method: str = "GET", 
    parse: None | EllipsisType | bool | Callable = None, 
    raise_for_status: bool = True, 
    timeout: None | float = 60, 
    **request_kwargs, 
):
    request_kwargs.pop("stream", None)
    try:
        resp = urlopen(
            url=url, 
            method=method, 
            timeout=timeout, 
            **request_kwargs, 
        )
    except HTTPError as e:
        if raise_for_status:
            raise
        resp = getattr(e, "file")
    if parse is None:
        return resp
    elif parse is ...:
        resp.close()
        return resp
    with resp:
        if isinstance(parse, bool):
            data = decompress_response(resp)
            if parse:
                content_type = resp.headers.get("Content-Type", "")
                if content_type == "application/json":
                    return loads(data)
                elif content_type.startswith("application/json;"):
                    return loads(data.decode(get_charset(content_type)))
                elif content_type.startswith("text/"):
                    return data.decode(get_charset(content_type))
            return data
        else:
            ac = argcount(parse)
            with resp:
                if ac == 1:
                    return parse(resp)
                else:
                    return parse(resp, decompress_response(resp))


def download(
    url: str, 
    file: bytes | str | PathLike | SupportsWrite[bytes] = "", 
    resume: bool = False, 
    chunksize: int = COPY_BUFSIZE, 
    headers: None | Mapping[str, str] = None, 
    make_reporthook: None | Callable[[None | int], Callable[[int], Any] | Generator[int, Any, Any]] = None, 
    **urlopen_kwargs, 
) -> str | SupportsWrite[bytes]:
    """Download a URL into a file.

    Example::

        1. use `make_reporthook` to show progress:

            You can use the following function to show progress for the download task

            .. code: python

                from time import perf_counter

                def progress(total=None):
                    read_num = 0
                    start_t = perf_counter()
                    while True:
                        read_num += yield
                        speed = read_num / 1024 / 1024 / (perf_counter() - start_t)
                        print(f"\r\x1b[K{read_num} / {total} | {speed:.2f} MB/s", end="", flush=True)

            Or use the following function for more real-time speed

            .. code: python

                from collections import deque
                from time import perf_counter
    
                def progress(total=None):
                    dq = deque(maxlen=64)
                    read_num = 0
                    dq.append((read_num, perf_counter()))
                    while True:
                        read_num += yield
                        cur_t = perf_counter()
                        speed = (read_num - dq[0][0]) / 1024 / 1024 / (cur_t - dq[0][1])
                        print(f"\r\x1b[K{read_num} / {total} | {speed:.2f} MB/s", end="", flush=True)
                        dq.append((read_num, cur_t))
    """
    if headers:
        headers = {**headers, "Accept-encoding": "identity"}
    else:
        headers = {"Accept-encoding": "identity"}

    if chunksize <= 0:
        chunksize = COPY_BUFSIZE

    resp: HTTPResponse = urlopen(url, headers=headers, **urlopen_kwargs)
    content_length = get_length(resp)
    if content_length == 0 and is_chunked(resp):
        content_length = None

    fdst: SupportsWrite[bytes]
    if hasattr(file, "write"):
        file = fdst = cast(SupportsWrite[bytes], file)
    else:
        file = abspath(fsdecode(file))
        if isdir(file):
            file = joinpath(file, get_filename(resp, "download"))
        try:
            fdst = open(file, "ab" if resume else "wb")
        except FileNotFoundError:
            makedirs(dirname(file), exist_ok=True)
            fdst = open(file, "ab" if resume else "wb")

    filesize = 0
    if resume:
        try:
            fileno = getattr(fdst, "fileno")()
            filesize = fstat(fileno).st_size
        except (AttributeError, OSError):
            pass
        else:
            if filesize == content_length:
                return file
            if filesize and is_range_request(resp):
                if filesize == content_length:
                    return file
            elif content_length is not None and filesize > content_length:
                raise OSError(
                    errno.EIO, 
                    f"file {file!r} is larger than url {url!r}: {filesize} > {content_length} (in bytes)", 
                )

    reporthook_close: None | Callable = None
    if callable(make_reporthook):
        reporthook = make_reporthook(content_length)
        if isgenerator(reporthook):
            reporthook_close = reporthook.close
            next(reporthook)
            reporthook = reporthook.send
        else:
            reporthook_close = getattr(reporthook, "close", None)
        reporthook = cast(Callable[[int], Any], reporthook)
    else:
        reporthook = None

    try:
        if filesize:
            if is_range_request(resp):
                resp.close()
                resp = urlopen(url, headers={**headers, "Range": "bytes=%d-" % filesize}, **urlopen_kwargs)
                if not is_range_request(resp):
                    raise OSError(errno.EIO, f"range request failed: {url!r}")
                if reporthook is not None:
                    reporthook(filesize)
            elif resume:
                for _ in bio_skip_iter(resp, filesize, callback=reporthook):
                    pass

        fsrc_read = resp.read 
        fdst_write = fdst.write
        while (chunk := fsrc_read(chunksize)):
            fdst_write(chunk)
            if reporthook is not None:
                reporthook(len(chunk))
    finally:
        resp.close()
        if callable(reporthook_close):
            reporthook_close()

    return file

