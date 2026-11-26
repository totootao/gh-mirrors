# gh-mirrors

GitHub 加速节点清单，复刻 [LX-M-Music.github.io](https://github.com/LX-M-Music/LX-M-Music.github.io) 的 `fetch-mirrors.mjs` 采集机制，并增强：**每个节点每日真实下载实测可用性**。

## 数据来源（多源合并去重）

| 来源 | 方式 |
|---|---|
| moretools.app/zh-CN/github-proxy | SSR HTML 内嵌镜像列表正则提取 |
| github.akams.cn | Next.js chunk 中 `value:"host"` 正则提取 |
| ghproxy.totootao.top | 自有代理，固定加入 |

## 与 LX-M 的差异

- LX-M：只负责"发现"，不做可用性验证；触发时机为构建前。
- 本仓库：**每日 cron（北京时间 09:00）采集 + 对每个节点实测下载 GitHub raw 文件**，输出可用/不可用分列表及延迟数据。

## 使用

- 可用节点清单：[`data/mirrors.json`](./data/mirrors.json)（字段：`usable` / `dead` / `latency` / `ownProxy`）
- 代理用法：`https://<节点>/https://raw.githubusercontent.com/<owner>/<repo>/<分支>/<文件>`
- 手动触发：Actions → fetch-mirrors → Run workflow
