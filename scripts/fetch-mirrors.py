#!/usr/bin/env python3
"""采集公共 GitHub 加速节点，生成 mirrors.json（复刻 LX-M-Music.github.io 的 fetch-mirrors.mjs 并增强）。

数据来源（多源合并去重）：
  1. moretools.app 的 github-proxy 页面（SSR HTML 内嵌镜像列表）
  2. github.akams.cn（Next.js chunk 中的节点列表）
  3. 自有代理 ghproxy.totootao.top 固定加入
  4. 老清单累积保留（可用节点不轻易出局，连续 3 次失败才移入 dead；dead 节点每天复测可复活）

每个节点实测三种代理能力（App 检查更新走 API、下载 APK 走 release、兜底走 raw）：
  raw     = 代理 raw.githubusercontent.com 文件
  release = 代理 github.com releases/download 探针资产
  api     = 代理 api.github.com 接口
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
MAX_CONSECUTIVE_FAILS = 3  # 老池节点连续失败这么多次才移入 dead（防误杀时好时坏的节点）

# 三能力探针（都指向本仓库，小文件，公共可访问）
PROBE_URL_RAW = "https://raw.githubusercontent.com/totootao/gh-mirrors/main/probe.txt"
PROBE_URL_RELEASE = "https://github.com/totootao/gh-mirrors/releases/download/probe/probe.txt"
PROBE_URL_API = "https://api.github.com/repos/totootao/gh-mirrors"
PROBE_CONTENT = b"gh-mirrors capability probe"


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
    """实测节点三种代理能力，返回 (host, caps)；caps: {raw, release, api, latency}。"""
    caps = {"host": host, "raw": False, "release": False, "api": False, "latency": None}
    probes = (
        ("raw", f"https://{host}/{PROBE_URL_RAW}",
         lambda body: body.startswith(PROBE_CONTENT)),
        ("release", f"https://{host}/{PROBE_URL_RELEASE}",
         lambda body: body.startswith(PROBE_CONTENT)),
        ("api", f"https://{host}/{PROBE_URL_API}",
         lambda body: b"gh-mirrors" in body[:4096]),
    )
    for cap, url, check in probes:
        try:
            r = requests.get(url, headers={"user-agent": UA}, timeout=(5, 8))
            if r.status_code == 200 and check(r.content):
                caps[cap] = True
                if caps["latency"] is None:
                    caps["latency"] = round(r.elapsed.total_seconds(), 3)
        except Exception:
            pass
    return host, caps


def load_previous():
    """读仓库里现有的 mirrors.json（CI 每次构建前已 checkout 到位），无则返回 None。"""
    try:
        with open("data/mirrors.json", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) and isinstance(d.get("usable"), list) else None
    except Exception:
        return None


def main():
    prev = load_previous()
    # 兼容新旧两种 usable 结构（旧为字符串数组，新为对象数组）
    prev_usable = {n["host"] if isinstance(n, dict) else n for n in prev["usable"]} if prev else set()
    prev_dead = set(prev.get("dead", [])) if prev else set()
    prev_failing = prev.get("failing", {}) if prev else {}

    merged = collect()
    merged.add(OWN_PROXY)
    # 累积保留：老池的可用节点、不可用节点全部保留参测（复活即自动回可用列表，清单只增不减）
    merged |= prev_usable
    merged |= prev_dead
    hosts = sorted(h for h in merged if HOST_RE.match(h))
    print(f"合并去重后共 {len(hosts)} 个候选节点（老池可用 {len(prev_usable)}，老池不可用 {len(prev_dead)}）")
    print("开始实测三能力（raw/release/api）……")

    results = {}
    with cf.ThreadPoolExecutor(max_workers=24) as ex:
        for host, caps in ex.map(test_node, hosts):
            results[host] = caps

    def any_ok(h):
        c = results[h]
        return c["raw"] or c["release"] or c["api"]

    passed = [h for h in hosts if any_ok(h)]
    failing = {}    # 观察期：老池可用节点今天全挂但连续失败未达阈值，仍留在 usable
    probation = []
    dead = []
    for h in hosts:
        if any_ok(h):
            continue
        if h in prev_usable:
            n = prev_failing.get(h, 0) + 1
            if n >= MAX_CONSECUTIVE_FAILS:
                dead.append(h)
            else:
                probation.append(h)
                failing[h] = n
        else:
            dead.append(h)
    # usable = 今天实测通过的（按延迟升序，节点对象） + 观察期老节点（保持稳定次序）
    usable_nodes = [
        dict(results[h], host=h)
        for h in sorted(passed, key=lambda h: results[h]["latency"] if results[h]["latency"] is not None else 999.0)
    ]
    usable_nodes += [dict(results[h], host=h, latency=None) for h in prev_usable if h in probation]
    dead = sorted(set(dead))
    n_release = sum(1 for n in usable_nodes if n["release"])
    n_api = sum(1 for n in usable_nodes if n["api"])
    print(f"实测通过 {len(passed)}，观察期 {len(probation)}，不可用 {len(dead)} / {len(hosts)}")
    print(f"能力分布：raw {sum(1 for n in usable_nodes if n['raw'])} · release {n_release} · api {n_api}")

    out = {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "ownProxy": OWN_PROXY,
        "maxConsecutiveFails": MAX_CONSECUTIVE_FAILS,
        "total": len(hosts),
        "usableCount": len(usable_nodes),
        "usable": usable_nodes,
        "failing": failing,
        "dead": dead,
    }
    with open("data/mirrors.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("已写入 data/mirrors.json")


if __name__ == "__main__":
    main()
