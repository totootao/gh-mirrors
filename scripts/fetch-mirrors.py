#!/usr/bin/env python3
"""采集公共 GitHub 加速节点，生成 mirrors.json（复刻 LX-M-Music.github.io 的 fetch-mirrors.mjs）。

数据来源：
  1. moretools.app 的 github-proxy 页面（SSR HTML 内嵌镜像列表）
  2. github.akams.cn（Next.js chunk 中的节点列表）
另外：
  - 把自有代理 ghproxy.totootao.top 固定加入
  - 对每个节点做真实下载实测（1MB 文件取头 256KB），标记可用性
"""
import json
import re
import sys
import time
import concurrent.futures as cf
from urllib.parse import urlparse

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
TIMEOUT = 20
HOST_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$", re.I)
OWN_PROXY = "ghproxy.totootao.top"
# 用于实测的公开测试文件（repo 内 1MB 左右的固定文件）
TEST_URL = "https://raw.githubusercontent.com/lyswhut/lx-music-desktop/master/CHANGELOG.md"
TEST_PROBE = "https://api.github.com/repos/lyswhut/lx-music-desktop"


def get_text(url):
    r = requests.get(url, headers={"user-agent": UA,
                                   "accept": "text/html,application/json,*/*"},
                     timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


def from_moretools():
    """每条镜像渲染为 <span class="...font-mono...">https://host/</span>"""
    html = get_text("https://www.moretools.app/zh-CN/github-proxy")
    hosts = set()
    for m in re.finditer(
            r'<span class="min-w-0 break-all font-mono text-sm text-foreground">(https?://[^<]+?)</span>',
            html):
        try:
            hosts.add(urlparse(m.group(1)).hostname.lower())
        except Exception:
            pass
    if not hosts:
        raise RuntimeError("moretools.app 页面结构变化，未解析到镜像")
    return "moretools.app", hosts


def from_akams():
    """抓首页拿到 chunk 清单，再从 chunk 中提取 value:"host\""""
    html = get_text("https://github.akams.cn/")
    chunks = sorted(set(re.findall(r"/_next/static/chunks/[a-z0-9]+\.js", html)))
    if not chunks:
        raise RuntimeError("github.akams.cn 未找到 chunk 清单")
    hosts = set()

    def fetch_chunk(chunk):
        js = get_text("https://github.akams.cn" + chunk)
        return set(m.group(1).lower()
                   for m in re.finditer(r'value:"([a-z0-9.-]+\.[a-z]{2,})"', js, re.I))

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for r in ex.map(fetch_chunk, chunks):
            hosts |= r
    if not hosts:
        raise RuntimeError("github.akams.cn chunk 中未解析到节点")
    return "github.akams.cn", hosts


def collect():
    merged = set()
    with cf.ThreadPoolExecutor(max_workers=2) as ex:
        futs = {ex.submit(fn): fn.__name__ for fn in (from_moretools, from_akams)}
        for fut in cf.as_completed(futs):
            try:
                name, hosts = fut.result()
                print(f"来源 {name}：{len(hosts)} 个节点")
                merged |= hosts
            except Exception as e:
                print(f"来源抓取失败（{futs[fut]}）：{e}", file=sys.stderr)
    return merged


def test_node(host):
    """实测节点：能否代理下载 GitHub raw 文件"""
    for target in (TEST_URL,):
        url = f"https://{host}/{target}" if "raw.githubusercontent" in target else target
        try:
            r = requests.get(url, headers={"user-agent": UA},
                             timeout=(5, 8), stream=True)
            if r.status_code == 200:
                chunk = next(r.iter_content(65536), b"")
                r.close()
                if chunk and (b"#" in chunk or b"release" in chunk or len(chunk) > 1024):
                    return host, True, r.elapsed.total_seconds()
            r.close()
        except Exception:
            pass
    return host, False, None


def main():
    merged = collect()
    # 固定加入自有代理
    merged.add(OWN_PROXY)
    hosts = sorted(h for h in merged if HOST_RE.match(h))
    print(f"合并去重后共 {len(hosts)} 个候选节点，开始实测可用性……")

    results = {}
    with cf.ThreadPoolExecutor(max_workers=24) as ex:
        for host, ok, sec in ex.map(test_node, hosts):
            results[host] = {"ok": ok}
            if sec is not None:
                results[host]["latency"] = round(sec, 3)

    usable = [h for h in hosts if results[h]["ok"]]
    dead = [h for h in hosts if not results[h]["ok"]]
    print(f"实测可用 {len(usable)} / {len(hosts)}")
    print(f"不可用 {len(dead)} 个")

    out = {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "ownProxy": OWN_PROXY,
        "total": len(hosts),
        "usableCount": len(usable),
        "usable": usable,
        "dead": dead,
        "latency": {h: results[h]["latency"] for h in usable if "latency" in results[h]},
    }
    with open("data/mirrors.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("已写入 data/mirrors.json")


if __name__ == "__main__":
    main()
