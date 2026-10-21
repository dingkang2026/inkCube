/**
 * Clipboard paste image filename builder（零依赖纯逻辑，tests/ClipboardImageName.test.ts
 * 直接 import 测试——与 fetch-pool-bounds.js 同模式，ipc-handlers.js 的
 * tasks:uploadClipboardImage 一行接线到它）。
 *
 * - JPEG 魔数嗅探（FF D8）定扩展名：vlm_client._MIME_BY_EXT 按扩展名映射真实
 *   MIME，恒 .png 会把 JPEG 字节谎报为 PNG 被严格校验端点拒收。
 * - 毫秒级时间戳：python 端点对同名文件覆盖写，秒级时间戳在同秒多图 /
 *   连续粘贴时第二张静默覆盖第一张。
 */
function clipboardImageName(data, now = new Date()) {
  const isJpeg = Buffer.isBuffer(data) && data.length > 2 && data[0] === 0xff && data[1] === 0xd8
  const pad = (n, w = 2) => String(n).padStart(w, '0')
  const stamp =
    `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}` +
    `-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}` +
    `-${pad(now.getMilliseconds(), 3)}`
  return `clipboard-${stamp}.${isJpeg ? 'jpg' : 'png'}`
}

module.exports = { clipboardImageName }
