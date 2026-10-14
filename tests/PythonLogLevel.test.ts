/** tests/PythonLogLevel.test.ts — python stderr/stdout 日志级别分类回归测试。
 *  回归背景：stderr 无内联级别的行一律 fallback ERROR，把 uvicorn 横幅
 *  （INFO: ...）与 tqdm 进度条每次启动都刷成 [ERROR]（2026-10-13 排障发现）。
 *  修复：两类已知例行输出归 INFO，其余无级别 stderr（traceback 等）维持 ERROR。
 *  被测模块 electron/py-log-level.js 为零依赖纯函数（从 python-manager 抽出，
 *  以绕开 vitest 无法 mock externalized require('electron') 的限制）。
 */
import { describe, it, expect } from 'vitest'
import { _pyLogLevel, _stderrFallbackLevel } from '../electron/py-log-level.js'

describe('_pyLogLevel 内联级别解析', () => {
  it('解析 python logging 行内级别并归一化', () => {
    expect(_pyLogLevel('2026-10-05 20:54:15 [WARNING] module: msg', 'INFO')).toBe('WARN')
    expect(_pyLogLevel('2026-10-05 20:54:15 [ERROR] module: msg', 'INFO')).toBe('ERROR')
    expect(_pyLogLevel('2026-10-05 20:54:15 [INFO] module: msg', 'ERROR')).toBe('INFO')
  })

  it('无内联级别时透传 fallback', () => {
    expect(_pyLogLevel('plain stdout line', 'INFO')).toBe('INFO')
    expect(_pyLogLevel('Traceback (most recent call last):', 'ERROR')).toBe('ERROR')
  })
})

describe('_stderrFallbackLevel 无级别 stderr 分类', () => {
  it('uvicorn 横幅归 INFO（2026-10-13 启动噪音回归）', () => {
    expect(_stderrFallbackLevel('INFO:     Started server process [3416]')).toBe('INFO')
    expect(_stderrFallbackLevel('INFO:     Uvicorn running on http://127.0.0.1:65170 (Press CTRL+C to quit)')).toBe('INFO')
    expect(_stderrFallbackLevel('INFO:     Application startup complete.')).toBe('INFO')
  })

  it('tqdm 进度条归 INFO（bge-m3 加载进度回归）', () => {
    expect(_stderrFallbackLevel('Loading weights:  27%|██▋      | 105/391 [00:00<00:00, 1013.58it/s]')).toBe('INFO')
    expect(_stderrFallbackLevel('Loading weights: 100%|██████████| 391/391 [00:00<00:00, 956.89it/s]')).toBe('INFO')
  })

  it('其余无级别 stderr 维持 ERROR（traceback 仍标红）', () => {
    expect(_stderrFallbackLevel('Traceback (most recent call last):')).toBe('ERROR')
    expect(_stderrFallbackLevel('torch.cuda.OutOfMemoryError: CUDA out of memory.')).toBe('ERROR')
  })
})
