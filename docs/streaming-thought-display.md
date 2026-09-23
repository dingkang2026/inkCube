# 流式思考内容显示优化

## 问题背景

在任务执行过程中，当大模型返回 `content` 和 `tool_calls` 时，`content` 实际上是模型在调用工具前的"分析/思考"内容，而不是最终回答。

**之前的行为（Option B）**：
1. 流式期间：`content` 显示在消息泡中（带光标）
2. 流式完成后：发现 `tool_calls` 存在，将 `content` 移到分析卡片
3. 用户体验：内容"跳动"，从消息泡消失，出现在分析卡片

## 解决方案（Option D）

**核心思想**：内容不移动，只改样式。

### 实现细节

#### 1. 状态管理（useTaskChat.ts）

新增状态 `streamIsThought`：
- `false`：`streamContent` 是最终回答，显示为消息泡
- `true`：`streamContent` 是分析内容，显示为分析卡片样式

```typescript
const [streamIsThought, setStreamIsThought] = useState(false)

// 收到 thought 事件时
} else if (type === 'thought') {
  setStreamIsThought(true)  // 标记为分析内容，不清空 streamContent
}

// 收到 answer_chunk 事件时
} else if (type === 'answer_chunk') {
  setStreamIsThought(false)  // 新内容，重置标记
  setStreamContent((prev) => prev + (parsed.content || ''))
}
```

#### 2. 渲染逻辑（TaskMessageList.tsx）

根据 `streamIsThought` 决定渲染方式：

```tsx
{streamContent && streamIsThought ? (
  // 分析内容：渲染为分析卡片样式（可折叠，带"分析"标签）
  <StreamThoughtBlock content={streamContent} isGenerating={isGenerating} />
) : streamContent ? (
  // 最终回答：渲染为消息泡
  <div className={s.message}>
    <div className={s.bubble}>
      <Markdown content={streamContent} />
      {isGenerating && <span className={s.streamingCursor}>▍</span>}
    </div>
  </div>
) : null}
```

#### 3. StreamThoughtBlock 组件

新增组件，与分析卡片样式一致，但支持流式光标：

```tsx
function StreamThoughtBlock({ content, isGenerating }) {
  const [open, setOpen] = useState(true)  // 默认展开
  return (
    <div className={s.stepRow}>
      <button className={s.stepToggle} onClick={() => setOpen(!open)}>
        <span className={s.chevron}>▸</span>
        <span className={s.stepLabel}>分析</span>
      </button>
      {open && (
        <div className={s.stepDetail}>
          <Markdown content={content} />
          {isGenerating && <span className={s.streamingCursor}>▍</span>}
        </div>
      )}
    </div>
  )
}
```

### 优势

| 优势 | 说明 |
|------|------|
| **无延迟** | 流式期间即时显示，不需要等待流式完成 |
| **无跳动** | 内容位置不变，只改变样式 |
| **视觉区分** | 通过样式变化（可折叠卡片 vs 消息泡）区分分析和回答 |
| **支持 Markdown** | 使用相同的 `Markdown` 组件，支持 md/mermaid 渲染 |

### 数据流

```
LLM 流式响应
  ↓
answer_chunk 事件 → streamContent += delta, streamIsThought = false
  ↓
前端渲染：消息泡样式（带光标）
  ↓
thought 事件 → streamIsThought = true
  ↓
前端渲染：分析卡片样式（可折叠，带"分析"标签，带光标）
  ↓
流式完成 → 光标消失，内容保留
```

### 对比业界做法

**Claude Code（Anthropic API）**：
- 返回内容块数组：`[{type: "thinking"}, {type: "text"}, {type: "tool_use"}]`
- 每个块从到达那一刻就有明确的 `type`，不会歧义
- 不会"跳动"，因为类型一开始就确定

**我们的方案（OpenAI 兼容 API）**：
- `content` 和 `tool_calls` 是分离的字段
- `content` 有歧义：可能是回答，也可能是分析
- 通过 `thought` 事件消除歧义，动态改变样式

**方案选择**：
- Option A（延迟显示）：用户体验差，有延迟感
- Option B（先显示再移动）：内容跳动，体验不流畅 ← 之前的问题
- Option C（统一放分析卡）：最终回答也延迟，体验差
- **Option D（不移动，改样式）**：无延迟、无跳动、视觉区分 ← 当前方案

## 相关文件

- `src/features/task/useTaskChat.ts`：状态管理
- `src/features/task/TaskMessageList.tsx`：渲染逻辑
- `src/features/task/TaskChatView.tsx`：传递 props
- `src/features/task/TaskPage.tsx`：传递 props
- `src/App.tsx`：传递 props

## 测试建议

1. **工具调用场景**：
   - 发送需要工具调用的任务（如"生成示例文件"）
   - 验证分析内容在流式期间即时显示
   - 验证流式完成后内容不跳动，只改变样式
   - 验证可折叠/展开

2. **纯文本回答场景**：
   - 发送不需要工具调用的任务（如"解释 Python 的 GIL"）
   - 验证内容始终显示为消息泡样式
   - 验证不会出现"分析"标签

3. **多轮对话场景**：
   - 连续发送多个任务
   - 验证每次流式状态正确重置
   - 验证历史消息不受影响

4. **Markdown 渲染**：
   - 发送包含代码块、列表、表格的分析内容
   - 验证 Markdown 正确渲染
   - 发送包含 mermaid 图表的分析内容
   - 验证 mermaid 正确渲染
