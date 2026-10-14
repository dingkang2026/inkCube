---
name: browser-fetch
description: 用内置浏览器进行 LLM 驱动的多步网页操作（action 循环：导航/点击/填表/下拉/滚动/提取），支持 JavaScript 渲染页面与登录态复用。实时页面内容（定价、文档、公告、搜索、后台系统等）必须用本工具获取，禁止凭训练记忆回答；每步返回页面快照（正文截断 + 可交互元素清单），看快照决定下一步。提取正文落盘工作区返回摘要；需要登录的站点会提示用户在浏览器面板完成登录。
---

# browser-fetch — 网页抓取与浏览器自动化（V2 action 模式）

通过 CDP 接管应用内置 Chromium（嵌入面板 webview，用户全程可见、可随时人工接管）。**每步一个 action，返回页面快照，看快照决定下一步**——循环往复直至任务完成。

## action 一览

| action | 语法 | 说明 |
|---|---|---|
| open | `open <url> [--wait-for CSS] [--timeout 秒]` | 导航+等待+快照（一切交互的起点） |
| click | `click --text 文本 \| --selector CSS` | 点击可见文本或元素，点击后返回新快照 |
| type | `type --selector CSS (--text 文本\|--text-file 相对路径) [--submit]` | 填表；`--submit` 填后回车；长文本用 --text-file（先 file_ops 写文件） |
| select | `select --selector CSS --value 值` | 下拉选择（原生 select） |
| scroll | `scroll [--direction up\|down] [--amount 像素]` | 滚动触发懒加载，快照跟随 |
| extract | `extract [--selector CSS] [--wait-for CSS] [--save-to 名.md]` | 区域/全文提取，落盘+摘要（--wait-for 兼做显式等待） |
| close | `close` | 浏览器归位待命页（一轮操作结束后调用，礼貌释放） |

## 快照返回体

操作型 action 返回 `{action, url, title, page:{text(≤3000字符), truncated}, elements:[≤80条 {role, name, selector}], needs_login}`。`elements` 的 `selector` 与 `name`（可见文本）跨调用稳定——**直接用它们定位，不要猜选择器**；`remaining>0` 表示还有更多元素，先 scroll 再看。

## needs_login

返回 `{error, needs_login: true, domain}` 时：**告知用户**在对话卡片点「立即登录」或到 设置 → 网页登录态 完成登录，用户回复后再继续发 action（登录态自动生效）。不要自己重试。

## 使用建议

1. **先 open 再交互**：快照是唯一事实来源，未 open 的页面上不要假设任何元素存在
2. click/type 用快照里的 `--text 可见文本` 最稳；`--selector` 用于文本重复或不可见元素（输入框、下拉）
3. SPA 页面 open 时加 `--wait-for` 等内容渲染；搜索框用 `type … --submit` 省一轮 click
4. 提取正文一律 `extract`（落盘 `fetched/`，stdout 只有摘要）；更多内容用 file_ops 读落盘文件
5. 内网地址（localhost/192.168.x 等）一律拒绝，这是安全设计，不要重试内网 URL
6. 任务结束时调一次 `close` 归位
7. 上下文中的快照/落盘结果是唯一事实来源；用户追问细节而现有数据不含答案时，重新操作获取（scroll/extract），**禁止编造数字、档位名或页面结构**
