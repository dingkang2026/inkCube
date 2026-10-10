---
name: browser-fetch
description: 用内置浏览器抓取动态网页（JavaScript 渲染后的内容），支持 CSS 选择器截取与登录态复用。抓取正文落盘工作区，返回摘要；需要登录的站点会提示用户先完成登录。
---

# browser-fetch — 动态网页抓取

通过 CDP 接管应用内置 Chromium 渲染页面，抓取 **JavaScript 渲染后** 的内容（普通 HTTP 抓取拿不到的 SPA 页面用本工具）。正文写入 `<workspace>/fetched/`，stdout 只返回摘要——需要更多内容时用 file_ops 读取落盘文件。

## 用法

```
python scripts/main.py <url> [--selector CSS] [--wait-for CSS] [--timeout 秒] [--save-to 文件名.md]
```

| 参数 | 说明 |
|---|---|
| `url` | 目标页（http/https） |
| `--selector` | 只提取匹配该 CSS 选择器的区域（推荐：文章正文用 `article`、`main`、`.content` 等） |
| `--wait-for` | 等待该选择器出现后再提取（SPA 首屏异步渲染时必加，如 `--wait-for .article-body`） |
| `--timeout` | 页面加载/等待超时秒数，默认 20 |
| `--save-to` | 落盘文件名，默认 `<域名>-<时间戳>.md` |

## 返回体

- 成功：`{url, title, path, length, outline, needs_login}` —— `path` 为落盘文件（相对工作区），`outline` 为章节结构；按需用 file_ops 读取全文或片段
- 需要登录：`{error, needs_login: true, domain}` —— **告知用户**在对话卡片点「立即登录」或到 设置 → 网页登录态 完成该站点登录，然后重试本工具
- 依赖预装中：`{error, retryable: true}` —— 稍后直接重试

## 使用建议

1. 优先加 `--selector` 截取正文区域，避免整页导航/页脚噪音
2. SPA 页面先加 `--wait-for` 等内容渲染完成，超时再放宽
3. 同一站点多个页面：逐页调用本工具，每次返回的摘要足够判断是否需要读全文
4. 内网地址（localhost/192.168.x 等）一律拒绝，这是安全设计，不要重试内网 URL
