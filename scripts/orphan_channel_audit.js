/** scripts/orphan_channel_audit.js — IPC 孤儿通道全仓审计 v3（2026-10 列表分页改造随包更新）
 *  v2 修正 v1 三类 false positive：
 *  - 前端经常量（MODELS_IPC.status 等）invoke → 别名表解析成员表达式
 *  - 后端路径为模板串拼接（`/tasks/${id}/messages`）→ electron 文本归一化 ${..}→{} 再匹配
 *  - 事件类收窄为真 IPC 面：ipcMain.handle/on ↔ electronAPI.invoke/send；
 *    main→renderer 的 webContents.send/event.sender.send ↔ electronAPI.on
 *  v3 修正两类扫描盲区（列表分页改造引入/暴露）：
 *  - usePagedList({ channel: 'ns:name' }) 参数形态：分页 hook 把通道字面量作为参数传入，
 *    实际调用点在 hook 内部 invoke(channel)——按参数字面量计入前端调用面；
 *  - 别名表同名键覆盖：KnowledgeIpcChannels（knowledge:*）与 baseLib 侧同名映射
 *    （getFiles 等键二值互斥，运行时按 prop 切换）→ 同名键改多值集合，任一消费点
 *    即视为该键全部通道有消费候选（宽松口径，宁漏报不误报）；
 *  - main→renderer 的 send 正则放宽为「前导点 + 通道形态」，收编 wc.send / sender.send
 *    等短变量形态（menu:* 三通道曾误报）。
 *  v3.1 补三类调用面盲区（v3 收编后 A 组余 5 通道，逐一查证均有真实消费）：
 *  - 跨行调用 invoke( 换行接实参 ipc.getActiveFiles：逐行扫描失明 → 增全文
 *    正则补充扫描（字面量/成员表达式皆收）；
 *  - 常量中转 const PAUSE_CHANNEL = 'knowledge:queuePause' 后 invoke(PAUSE_CHANNEL)：
 *    纯标识符不落别名分支 → const 通道常量表 + invoke(标识符) 查表；
 *  - Channel 类 prop/默认值：JSX chatIpcChannel="baseLib:chat" 与签名默认值
 *    chatIpcChannel = 'knowledge:chat' → 「\w*[Cc]hannel\w*」形态收编；
 *  另 renderer 侧 on 收窄为 electronAPI.on（WikiGraphView 的 sigma 图库 DOM 事件
 *  同名 on 曾误报 C4；src 全部 IPC on 已核实均为 electronAPI.on 形态）。
 *  ⚠ 嵌入教训：exec 循环条件里禁止内联正则字面量——字面量每次求值重建、
 *  lastIndex 归零，命中一次即死循环（const 中转扫描曾在 useQueuePause.ts 挂死）；
 *  循环用正则必须提为变量（本文件 RES/konstRe/invIdRe 形态）。
 */
const fs = require('fs')
const path = require('path')

const ROOT = path.resolve(__dirname, '..')

function walk(dir, exts, out = []) {
  for (const name of fs.readdirSync(dir)) {
    if (name === 'node_modules' || name === 'dist' || name === 'dist-renderer' || name.startsWith('.')) continue
    const p = path.join(dir, name)
    const st = fs.statSync(p)
    if (st.isDirectory()) walk(p, exts, out)
    else if (exts.some((e) => name.endsWith(e))) out.push(p)
  }
  return out
}
const linesOf = (f) => fs.readFileSync(f, 'utf8').split(/\r?\n/)
const electronFiles = walk(path.join(ROOT, 'electron'), ['.js'])
const rendererFiles = walk(path.join(ROOT, 'src'), ['.ts', '.tsx'])
const CH = /[a-z][a-z0-9-]*:[a-z0-9-]+/ // 通道名形态（namespace:name）

// ── 别名表：src 全部「member: 'ns:name'」成员 → 通道 ──────────────
const alias = new Map()
for (const f of rendererFiles) {
  linesOf(f).forEach((line) => {
    // v3：改用 RegExp 构造器（字面量闭合「/」与调用「)」相邻易在补丁中错位）；
    // 字符类 [\x27\x22] 即 ['"]，捕获组序号不变（m[1]=键、m[2]=通道）
    const m = line.match(new RegExp('([A-Za-z_$][\\w$]*)\\s*:\\s*[\\x27\\x22]([a-z][\\w-]*:[\\w/-]+)[\\x27\\x22]'))
    if (!m || !CH.test(m[2])) return
    // v3：同名键改多值集合——knowledge:* 与 baseLib 同名映射二值互斥（运行时按
    // prop 切换），静态扫描无法定归属 → 任一消费点即视为该键全部通道有候选
    if (!alias.has(m[1])) alias.set(m[1], new Set())
    alias.get(m[1]).add(m[2])
  })
}

// ── invoke/send/on 调用点解析 ────────────────────────────────────
function parseCalls(files, fn, kind) {
  const calls = new Map() // channel -> [loc]
  const dynamic = []
  for (const f of files) {
    linesOf(f).forEach((line, i) => {
      const re = new RegExp(`\\b${fn}\\(\\s*['"]([^'"]+)['"]`)
      const m = line.match(re)
      if (m) {
        const ch = m[1]
        if (!calls.has(ch)) calls.set(ch, [])
        calls.get(ch).push(`${path.relative(ROOT, f)}:${i + 1}`)
        return
      }
      // v3：usePagedList({ channel: 'ns:name' })——分页 hook 把通道字面量作为参数
      // 传入，实际调用点在 hook 内部 invoke(channel)；按参数字面量计入调用面
      if (kind === 'invoke') {
        const hm = line.match(/\bchannel:\s*['"]([a-z][\w-]*:[\w/-]+)['"]/)
        if (hm) {
          if (!calls.has(hm[1])) calls.set(hm[1], [])
          calls.get(hm[1]).push(`${path.relative(ROOT, f)}:${i + 1} (via usePagedList channel)`)
          return
        }
      }
      // 别名表达式：invoke(MODELS_IPC.status) / send(FOO.bar)
      const am = line.match(new RegExp(`\\b${fn}\\(\\s*([A-Za-z_$][\\w$]*(?:\\.[A-Za-z_$][\\w$]*)+)`))
      if (am) {
        const segs = am[1].split('.')
        let hit = null
        for (let k = 0; k < segs.length && !hit; k++) {
          hit = alias.get(segs.slice(k).join('.')) || alias.get(segs[k]) || null
        }
        if (hit) {
          for (const ch of hit) {
            if (!calls.has(ch)) calls.set(ch, [])
            calls.get(ch).push(`${path.relative(ROOT, f)}:${i + 1} (via ${am[1]})`)
          }
        } else {
          dynamic.push(`${path.relative(ROOT, f)}:${i + 1}  ${am[1]}`)
        }
      }
    })
  }
  return { calls, dynamic }
}

const inv = parseCalls([...rendererFiles, ...electronFiles], 'invoke', 'invoke')
const sentByRenderer = parseCalls(rendererFiles, 'send', 'send')
const onInRenderer = parseCalls(rendererFiles, 'electronAPI\\.on', 'on') // v3.1 收窄：IPC 事件面仅 electronAPI.on，DOM/图库同名 on 不计入

// v3.1 补充扫描：跨行 invoke / 常量中转 / Channel 类 prop——只增调用面（宽松口径，
// 宁漏报不误报）。逐行扫描对跨行实参、纯标识符变量、prop 形态天然失明。
function scanSupplemental(files) {
  const calls = new Map()
  const add = (ch, loc) => {
    if (!calls.has(ch)) calls.set(ch, [])
    calls.get(ch).push(loc)
  }
  const resolveAlias = (expr) => {
    const segs = expr.split('.')
    for (let k = 0; k < segs.length; k++) {
      const hit = alias.get(segs.slice(k).join('.')) || alias.get(segs[k])
      if (hit) return hit
    }
    return null
  }
  const RES = [
    [/\binvoke\(\s*['"]([a-z][\w-]*:[\w/-]+)['"]/g, false], // 字面量（含跨行）
    [/\binvoke\(\s*([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+)/g, true], // 成员表达式→别名
    [/\b\w*[Cc]hannel\w*\s*[:=]\s*['"]([a-z][\w-]*:[\w/-]+)['"]/g, false], // channel:/xxxChannel= prop
  ]
  for (const f of files) {
    const text = fs.readFileSync(f, 'utf8')
    const lineNo = (idx) => text.slice(0, idx).split(/\r?\n/).length
    for (const [re, isAlias] of RES) {
      for (let m; (m = re.exec(text)); ) {
        if (isAlias) {
          const hit = resolveAlias(m[1])
          if (hit) for (const ch of hit) add(ch, `${path.relative(ROOT, f)}:${lineNo(m.index)} (v3.1)`)
        } else {
          add(m[1], `${path.relative(ROOT, f)}:${lineNo(m.index)} (v3.1)`)
        }
      }
    }
    // 常量中转：const X = 'ns:name' → invoke(X)。正则必须提出循环外：内联字面量
    // 每次迭代重建、lastIndex 归零，命中一次即死循环（useQueuePause.ts 曾挂死）
    const konstRe = /\bconst\s+([A-Za-z_$][\w$]*)\s*=\s*['"]([a-z][\w-]*:[\w/-]+)['"]/g
    const invIdRe = /\binvoke\(\s*([A-Za-z_$][\w$]*)\s*[),]/g
    const consts = new Map()
    for (let m; (m = konstRe.exec(text)); ) consts.set(m[1], m[2])
    if (consts.size) {
      for (let m; (m = invIdRe.exec(text)); ) {
        const ch = consts.get(m[1])
        if (ch) add(ch, `${path.relative(ROOT, f)}:${lineNo(m.index)} (v3.1 const)`)
      }
    }
  }
  return calls
}
for (const [ch, locs] of scanSupplemental([...rendererFiles, ...electronFiles])) {
  if (!inv.calls.has(ch)) inv.calls.set(ch, locs)
}

// main 侧注册
const handleReg = new Map()
const onReg = new Map()
const pushToRenderer = new Map()
for (const f of electronFiles) {
  linesOf(f).forEach((line, i) => {
    const loc = `${path.relative(ROOT, f)}:${i + 1}`
    let m = line.match(/ipcMain\.handle\(\s*['"]([^'"]+)['"]/)
    if (m) handleReg.set(m[1], loc)
    m = line.match(/ipcMain\.on\(\s*['"]([^'"]+)['"]/)
    if (m) onReg.set(m[1], loc)
    // v3：放宽为「任意前导点链 + .send(」，收编 wc.send / sender.send 等短变量
    // 形态（menu:* 三通道、activity:stream 曾漏采）；port.postMessage 单列
    m = line.match(/[.\w?]*\.send\(\s*['"]([a-z][\w-]*:[\w/-]+)['"]/) || line.match(/port\.postMessage\(\s*['"]([^'"]+)['"]/)
    if (m && CH.test(m[1])) pushToRenderer.set(m[1], loc)
  })
}

const out = (t) => console.log(t)
out('=== A. ipcMain.handle 注册但前端零 invoke（孤儿 handler）===')
let a = 0
for (const [ch, loc] of handleReg) if (!inv.calls.has(ch)) { out(`  ${ch}  @ ${loc}`); a++ }
out(`  (${a} 个 / 共 ${handleReg.size} 注册)`)

out('=== B. 前端 invoke 但无 ipcMain.handle（运行时必挂）===')
let b = 0
for (const [ch, locs] of inv.calls) if (!handleReg.has(ch)) { out(`  ${ch}  <- ${locs[0]}`); b++ }
out(`  (${b} 个)`)

out('=== C1. ipcMain.on 注册但前端零 send（孤儿监听）===')
let c1 = 0
for (const [ch, loc] of onReg) if (!sentByRenderer.calls.has(ch)) { out(`  ${ch}  @ ${loc}`); c1++ }
out(`  (${c1} 个 / 共 ${onReg.size} 注册)`)

out('=== C2. 前端 send 但无 ipcMain.on（发向虚空）===')
let c2 = 0
for (const [ch, locs] of sentByRenderer.calls) if (!onReg.has(ch)) { out(`  ${ch}  <- ${locs[0]}`); c2++ }
out(`  (${c2} 个)`)

out('=== C3. main→renderer send 但前端零 on（事件孤儿）===')
let c3 = 0
for (const [ch, loc] of pushToRenderer) if (!onInRenderer.calls.has(ch)) { out(`  ${ch}  @ ${loc}`); c3++ }
out(`  (${c3} 个 / 共 ${pushToRenderer.size})`)

out('=== C4. 前端 on 但 main 零 send（永不触发）===')
let c4 = 0
for (const [ch, locs] of onInRenderer.calls) if (!pushToRenderer.has(ch)) { out(`  ${ch}  <- ${locs[0]}`); c4++ }
out(`  (${c4} 个)`)

// ── D：python 端点 vs electron 归一化文本 ────────────────────────
const pyRoutes = new Map()
for (const f of walk(path.join(ROOT, 'python', 'routers'), ['.py'])) {
  let prefix = ''
  const lines = linesOf(f)
  lines.forEach((line) => {
    const pm = line.match(/APIRouter\([^)]*prefix\s*=\s*['"]([^'"]+)['"]/)
    if (pm) prefix = pm[1]
  })
  lines.forEach((line, i) => {
    const m = line.match(/@router\.(get|post|put|delete|patch)\(\s*['"]([^'"]*)['"]/)
    if (m) {
      const full = (m[2].startsWith('/') ? prefix + m[2] : prefix + '/' + m[2]).replace(/\{[^}:]+\}/g, '{}').replace(/\/+$/, '') || '/'
      if (!pyRoutes.has(full)) pyRoutes.set(full, `${path.relative(ROOT, f)}:${i + 1}`)
    }
  })
}
const elNorm = electronFiles.map((f) => fs.readFileSync(f, 'utf8')).join('\n').replace(/\$\{[^}]*\}/g, '{}')
out('=== D. python 端点但 electron 归一化文本零引用（孤儿端点候选）===')
let d = 0
for (const [rt, loc] of pyRoutes) {
  if (!elNorm.includes(rt)) { out(`  ${rt}  @ ${loc}`); d++ }
}
out(`  (${d} 个 / 共 ${pyRoutes.size} 端点)`)

out('=== E. 未解析的别名调用（人工核对）===')
for (const x of [...inv.dynamic, ...sentByRenderer.dynamic, ...onInRenderer.dynamic]) out(`  ${x}`)
