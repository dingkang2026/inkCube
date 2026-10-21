// bounds 桥纯逻辑（2.8 §6.2 V2.1-R3）：零依赖、独立于 electron 可单测。
// 坐标系：渲染层 getBoundingClientRect 返回 CSS px，物理位置 = CSS × dpr，
// 而 dpr = 显示器 scale(S) × zoomFactor(z)。WebContentsView.setBounds 坐标
// 是 DIP，物理 = DIP × S。对齐条件 bounds_DIP = CSS × dpr / S = **CSS × z**
// （与显示器缩放无关）。视图缩放（视图菜单/Ctrl+滚轮 → zoomLevel ±0.5 →
// z = 1.2^level）非 1 时 CSS 值直传 DIP 会偏移 CSS×(1-z)：zoomOut 半步
// (z≈0.913) 下 WCV 右移、右缘被窗口裁切（2026-10-14 截图实证）。
function handleSetBounds(view, sender, rect) {
  if (!view || !rect || typeof rect.x !== 'number') return
  const z = sender?.getZoomFactor?.() || 1
  view.setBounds({
    x: Math.round(rect.x * z),
    y: Math.round(rect.y * z),
    width: Math.max(1, Math.round(rect.width * z)),
    height: Math.max(1, Math.round(rect.height * z)),
  })
}

module.exports = { handleSetBounds }
