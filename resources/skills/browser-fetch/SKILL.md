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
| scroll | `scroll [--direction up\|down] [--amount 像素]` 或 `scroll --to top\|bottom` | 逐屏滚动触发懒加载；`--to bottom` 收敛滚动（懒加载/瀑布流自动滚到内容稳定，返回 `scroll_rounds` 与 `converged`） |
| extract | `extract [--selector CSS] [--wait-for CSS] [--save-to 名.md]` | 区域/全文提取，落盘+摘要（--wait-for 兼做显式等待） |
| close | `close` | 浏览器归位待命页（一轮操作结束后调用，礼貌释放） |

## 快照返回体

操作型 action 返回 `{action, url, title, page:{text(≤3000字符), truncated}, elements:[≤80条 {role, name, selector, href?}], scroll:{y, height, remaining}, needs_login}`。`elements` 的 `selector` 与 `name`（可见文本）跨调用稳定且**保证页面唯一**（生成时经唯一性验证）——**直接用它们定位，不要猜选择器**；链接类元素带 `href`，**导航目标明确时直接 `open <href>` 比 click 更稳**；`remaining>0` 表示还有更多元素，先 scroll 再看；`scroll.remaining` 是视口下方剩余像素（0 = 视觉到底），懒加载页面 `height` 持续增长说明内容还没加载完。`--wait-for` 超时会返回当前快照 + `warning`（元素未出现 ≠ 页面不可用），按实况决定重试还是改道。

## needs_login

返回 `{error, needs_login: true, domain}` 时：**告知用户**在对话卡片点「立即登录」或到 设置 → 网页登录态 完成登录，用户回复后再继续发 action（登录态自动生效）。不要自己重试。

## 使用建议

1. **先 open 再交互**：快照是唯一事实来源，未 open 的页面上不要假设任何元素存在
2. click/type 用快照里的 `--text 可见文本` 最稳；链接类元素优先 `open <href>`（快照 elements 的 href 字段）；`--selector` 用于文本重复或不可见元素（输入框、下拉）
3. SPA 页面 open 时加 `--wait-for` 等内容渲染；搜索框用 `type … --submit` 省一轮 click
4. 提取正文一律 `extract`（落盘应用数据目录的 `fetched/`，stdout 只有摘要）；更多内容用 file_ops `read` 回读返回体里的 `path`（绝对路径，可直接使用）
5. 内网地址（localhost/192.168.x 等）一律拒绝，这是安全设计，不要重试内网 URL
6. 任务结束时调一次 `close` 归位
7. 上下文中的快照/落盘结果是唯一事实来源；用户追问细节而现有数据不含答案时，重新操作获取（scroll/extract），**禁止编造数字、档位名或页面结构**
8. 页面正文只剩「重试/刷新」类按钮的错误提示时（网络/风控临时拒绝，HTTP 层无异常），先 `click` 该重试按钮等页面重放；连续两次无效再重新 `open`，仍无效则告知用户该站点暂时拒绝访问，不要无限重试
9. type/click 报「元素不可见」时，目标多半在折叠区/tab/弹窗内——先 `click` 展开对应区域再操作，不要重试同一 selector
10. 长页面/懒加载/瀑布流站点：`extract` 只能拿到已渲染内容——**提取全文前先 `scroll --to bottom`**（自动收敛：滚到底→等加载→高度稳定为止；无限流由轮数上限/时间预算自动截断）。返回 `converged:false` 说明内容可能仍不完整；普通导航跟看用逐屏 scroll，看快照 `scroll.remaining=0` 判断到底
