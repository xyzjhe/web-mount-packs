#!/usr/bin/env python3
# encoding: utf-8

__author__ = "ChenyangGao <https://github.com/ChenyangGao>"
__all__ = ["getdir", "get_dir_url", "getfile", "get_file_url", "iterdir"]

from collections.abc import Callable, Iterator
from datetime import datetime
from html import unescape
from json import load
from posixpath import join as joinpath
from re import compile as re_compile
from typing import Final, Optional
from urllib.parse import unquote, urlsplit

try:
    from .util.response import get_filename
    from .util.urlopen import urlopen
except ImportError:
    from util.response import get_filename # type: ignore
    from util.urlopen import urlopen # type: ignore


CRE_VALUE_search: Final = re_compile(r'(?<=value=")[^"]+').search
CRE_TEXT_search: Final = re_compile(r"(?<=>)(?=\S)[^<]+").search
CRE_HREF_search: Final = re_compile(r'(?<=href=")[^"]+').search


def getdir(
    id_or_url: str, 
    /, 
    password: str = "", 
    folder_id: str = "", 
    headers: dict = {}, 
) -> dict:
    "获取目录信息"
    api = "https://webapi.ctfile.com/getdir.php"
    path_prefix = "dir"
    if id_or_url.startswith(("http://", "https://")):
        url = id_or_url
        urlp = urlsplit(url)
        path = unquote(urlp.path).rstrip("/")
        if not path.startswith("/dir/"):
            fid = path[5:]
        elif not path.startswith("/d/"):
            fid = path[3:]
            path_prefix = "d"
        else:
            raise ValueError(f"wrong dir url: {url!r}")
    else:
        fid = id_or_url
    if not folder_id:
        try:
            folder_id = fid.split("-")[1]
        except IndexError:
            raise ValueError(f"invalid id or url: {id_or_url!r}")
    params = {"path": path_prefix, "d": fid, "passcode": password, "folder_id": folder_id}
    return load(urlopen(api, params=params, headers=headers))


def get_dir_url(
    id_or_url_or_data: str | dict, 
    /, 
    password: str = "", 
    folder_id: str = "", 
    headers: dict = {}, 
) -> str:
    "获取目录链接"
    if isinstance(id_or_url_or_data, str):
        json = getdir(id_or_url_or_data, password, folder_id, headers=headers)
    else:
        json = id_or_url_or_data
    try:
        return "https://webapi.ctfile.com" + json["file"]["url"]
    except KeyError as e:
        raise ValueError("can't get dir url, may be wrong url or password") from e


def attrs_from_dir_url(url: str, /, headers: Optional[dict] = None) -> Iterator[dict]:
    def parse(item):
        try:
            fid = CRE_VALUE_search(item[0])[0] # type: ignore
            return {
                "id": int(fid[1:]), 
                "isdir": fid[0] == "d", 
                "name": unescape(CRE_TEXT_search(item[1])[0]), # type: ignore
                "size": None if item[2] == "- -" else item[2], 
                "tempdir": CRE_HREF_search(item[1])[0][3:], # type: ignore
            }
        except TypeError as e:
            raise ValueError(f"invalid url: {url!r}") from e
    return map(parse, load(urlopen(url, headers=headers))["aaData"])


def getfile(
    id_or_url: str, 
    /, 
    password: str = "", 
    headers: dict = {}, 
) -> dict:
    "获取文件信息"
    api = "https://webapi.ctfile.com/getfile.php"
    path_prefix = "file"
    if id_or_url.startswith(("http://", "https://")):
        url = id_or_url
        urlp = urlsplit(url)
        path = unquote(urlp.path).rstrip("/")
        if path.startswith("/file/"):
            fid = path[6:]
        elif path.startswith("/f/"):
            fid = path[3:]
            path_prefix = "f"
        else:
            raise ValueError(f"wrong file url: {url!r}")
    else:
        fid = id_or_url
    params = {"path": path_prefix, "f": fid, "passcode": password}
    return load(urlopen(api, params=params, headers=headers))


def get_file_url(
    id_or_url_or_data: str | dict, 
    /, 
    password: str = "", 
    headers: dict = {}, 
) -> str:
    "获取文件的下载链接"
    api = "https://webapi.ctfile.com/get_file_url.php"
    if isinstance(id_or_url_or_data, str):
        json = getfile(id_or_url_or_data, password)
    else:
        json = id_or_url_or_data
    info = json["file"]
    params = {"uid": info["userid"], "fid": info["file_id"], "file_chk": info["file_chk"]}
    resp = load(urlopen(api, params=params, headers=headers))
    if "url" in resp:
        raise OSError(resp)
    return resp["downurl"]


def attr_from_file_url(url: str, /, headers: Optional[dict] = None) -> dict:
    "请求下载链接，获取文件信息"
    resp = urlopen(url, method="HEAD", headers=headers)
    resp_headers = resp.headers
    date = datetime.strptime(resp_headers["date"], "%a, %d %b %Y %H:%M:%S %Z")
    if "last-modified" in resp_headers:
        last_modified = datetime.strptime(resp_headers["last-modified"], "%a, %d %b %Y %H:%M:%S %Z")
    else:
        last_modified = date
    return {
        "filename": get_filename(resp), 
        "size": int(resp_headers["content-length"]), 
        "created_time": date, 
        "modified_time": last_modified, 
        "access_time": last_modified, 
        "download_url": resp.url, 
    }


def iterdir(
    id_or_url: str, 
    /, 
    password: str = "", 
    folder_id: str = "", 
    predicate: Optional[Callable] = None, 
    files_only: Optional[bool] = None, 
    show_download: bool = False, 
    show_detail: bool = False, 
    relpath: str = "", 
    headers: dict = {}, 
) -> Iterator[dict]:
    "获取分享链接中的条目信息，迭代器"
    if id_or_url.startswith(("http://", "https://")):
        url = id_or_url
        urlp = urlsplit(url)
        path = unquote(urlp.path).rstrip("/")
        if path.startswith(("/f/", "/file/")):
            if files_only != False:
                if path.startswith("/f/"):
                    fid = path[3:]
                else:
                    fid = path[6:]
                data = getfile(fid, password, headers=headers)
                info = data["file"]
                attr = {
                    "id": info["file_id"], 
                    "isdir": False, 
                    "name": info["file_name"], 
                    "size": info["file_size"], 
                    "time": info["file_time"], 
                    "userid": info["userid"], 
                    "file_chk": info["file_chk"], 
                    "file_dir": info["file_dir"], 
                    "tempdir": fid, 
                }
                if show_detail or show_download:
                    while True:
                        try:
                            download_url = get_file_url(data, headers=headers)
                            break
                        except KeyError:
                            pass
                    attr["download_url"] = download_url
                    if show_detail:
                        attr.update(attr_from_file_url(download_url))
                attr["relpath"] = joinpath(relpath, attr["name"])
                if not predicate or predicate(attr):
                    yield attr
            return
        elif path.startswith(("/d/", "/dir/")):
            if path.startswith("/d/"):
                fid = path[3:]
            else:
                fid = path[5:]
            if not folder_id and urlp.query:
                folder_id = urlp.query
                idx = folder_id.find("&")
                if idx > -1:
                    folder_id = folder_id[:idx]
        else:
            raise ValueError(f"invalid id or url: {id_or_url!r}")
    else:
        fid = id_or_url
    dir_url = get_dir_url(fid, password, folder_id, headers=headers)
    dirs: list[str] = []
    for attr in attrs_from_dir_url(dir_url, headers=headers):
        if attr["isdir"]:
            attr.pop("tempdir", None)
            if show_detail:
                info = getdir(fid, password, attr["id"], headers=headers)["file"]
                attr.update({
                    "folder_name": info["folder_name"], 
                    "time": info["folder_time"], 
                    "userid": info["userid"], 
                    "file_chk": info["file_chk"], 
                })
        elif files_only == False:
            continue
        elif show_detail or show_download:
            data = getfile(attr["tempdir"], password, headers=headers)
            info = data["file"]
            attr.update({
                "id": info["file_id"], 
                "isdir": False, 
                "time": info["file_time"], 
                "userid": info["userid"], 
                "file_chk": info["file_chk"], 
                "file_dir": info["file_dir"], 
            })
            while True:
                try:
                    download_url = get_file_url(data, headers=headers)
                    break
                except KeyError:
                    pass
            attr["download_url"] = download_url
            if show_detail:
                attr.update(attr_from_file_url(download_url, headers=headers))
        sub_relpath = attr["relpath"] = joinpath(relpath, attr["name"])
        if not predicate or predicate(attr):
            if attr["isdir"]:
                dirs.append(attr["id"])
                if files_only:
                    continue
            yield attr
        for sub_folder_id in dirs:
            yield from iterdir(
                fid, 
                password, 
                sub_folder_id, 
                predicate=predicate, 
                files_only=files_only, 
                show_download=show_download, 
                show_detail=show_detail, 
                relpath=relpath, 
                headers=headers, 
            )

