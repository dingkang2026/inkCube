/** src/i18n/errorCodes.ts — 后端错误 code → 前端双语文案映射（D1 方案 A）。
 *
 * 协议（0.1dev-guide §错误契约）：
 * 1. Router 同步返回：任何面向用户的错误都带 `code` 字段（大写蛇形），
 *    中文原文保留在 `error`/`message` 字段作为未知 code 时的 fallback 与日志线索。
 * 2. 长时活动落库：`errors.actionable()` 对 InkCubeError 产出
 *    `INKCUBE_CODE:<CODE>|<detail>` 结构化串落库（error_message / 助手消息），
 *    前端渲染前经 translateStoredError 还原为当前语言；非 InkCubeError 透传
 *    原文（历史数据 / 第三方异常串）自动走 fallback。
 *
 * 映射缺失时：apiErrorText 回退 fallback 原文，translateStoredError 回退
 * code 本身——后端新增 code 未同步前端时降级可读，不阻塞。
 */

type TFn = (key: string, opts?: Record<string, unknown>) => string

interface ErrorI18nEntry {
  /** errors.codes 段下的主文案 key */
  msg: string
  /** 可选：errors.codes 段下的建议文案 key（useErrorHandler suggestion 槽位） */
  sug?: string
}

export const ERROR_CODE_I18N: Record<string, ErrorI18nEntry> = {
  // ── tasks ──
  TASK_NOT_FOUND: { msg: 'taskNotFound' },
  TASK_QUOTA_REACHED: { msg: 'taskQuotaReached' },
  TASK_LIMIT_REACHED: { msg: 'taskLimitReached' },
  TASK_RUNNING: { msg: 'taskRunning' },
  TASK_NOT_RUNNING: { msg: 'taskNotRunning' },
  TASK_NOT_PAUSABLE: { msg: 'taskNotPausable' },
  PAUSE_FAILED: { msg: 'pauseFailed' },
  NO_RESUMABLE_STATE: { msg: 'noResumableState' },
  WORKSPACE_NOT_FOUND: { msg: 'workspaceNotFound' },
  INVALID_TITLE: { msg: 'invalidTitle' },
  INVALID_MODE: { msg: 'invalidMode' },
  PATH_REQUIRED: { msg: 'pathRequired' },
  PATH_OUTSIDE_WORKSPACE: { msg: 'pathOutsideWorkspace' },
  FILE_TOO_LARGE: { msg: 'fileTooLarge' },
  // ── skills ──
  SKILL_MANIFEST_MISSING: { msg: 'skillManifestMissing' },
  INVALID_SOURCE_TYPE: { msg: 'invalidSourceType' },
  SOURCE_PATH_NOT_FOUND: { msg: 'sourcePathNotFound' },
  SKILL_NAME_EXISTS: { msg: 'skillNameExists' },
  INVALID_GIT_URL: { msg: 'invalidGitUrl' },
  GIT_REPO_NAME_FAILED: { msg: 'gitRepoNameFailed' },
  SKILL_DIR_EXISTS: { msg: 'skillDirExists' },
  GIT_CLONE_FAILED: { msg: 'gitCloneFailed' },
  GIT_NOT_FOUND: { msg: 'gitNotFound' },
  GIT_CLONE_TIMEOUT: { msg: 'gitCloneTimeout' },
  SKILL_NOT_FOUND: { msg: 'skillNotFound' },
  SKILL_BUILTIN_PROTECTED: { msg: 'skillBuiltinProtected' },
  // ── web logins ──
  INVALID_DOMAIN: { msg: 'invalidDomain' },
  SITE_NOT_FOUND: { msg: 'siteNotFound' },
  // ── schedules ──
  RUN_NOT_FOUND: { msg: 'runNotFound' },
  RUN_RUNNING: { msg: 'runRunning' },
  // ── knowledge ──
  FILE_DUPLICATE: { msg: 'fileDuplicate' },
  MANUALLY_ARCHIVED: { msg: 'manuallyArchived' },
  // ── projects（归档 2.1 §归档）──
  PROJECT_HAS_ACTIVE_WORK: { msg: 'projectHasActiveWork' },
  PROJECT_NOT_ARCHIVED: { msg: 'projectNotArchived' },
  PROJECT_ALREADY_ACTIVE: { msg: 'projectAlreadyActive' },
  // ── models ──
  MODEL_ALREADY_DOWNLOADING: { msg: 'modelAlreadyDownloading' },
  MODEL_ALL_BUSY: { msg: 'modelAllBusy' },
  // ── settings personal_test ──
  CONNECTED: { msg: 'connected' },
  CONNECTION_FAILED: { msg: 'connectionFailed' },
  // ── wiki findings ──
  FINDING_NOT_ADJUDICABLE: { msg: 'findingNotAdjudicable' },
  FINDING_ALREADY_RESOLVED: { msg: 'findingAlreadyResolved' },
  FINDING_NO_EVIDENCE: { msg: 'findingNoEvidence' },
  CHOSEN_INDEX_OUT_OF_RANGE: { msg: 'chosenIndexOutOfRange' },
  EVIDENCE_QUOTE_MISSING: { msg: 'evidenceQuoteMissing' },
  // ── reports ──
  TEMPLATE_PRESET_IMMUTABLE: { msg: 'templatePresetImmutable' },
  TEMPLATE_PRESET_NO_UPGRADE: { msg: 'templatePresetNoUpgrade' },
  REPORT_GENERATING: { msg: 'reportGenerating' },
  REPORT_CONCURRENT_LIMIT: { msg: 'reportConcurrentLimit' },
  UNSUPPORTED_SAMPLE_FORMAT: { msg: 'unsupportedSampleFormat' },
  COMPILING_IN_PROGRESS: { msg: 'compilingInProgress' },
  COLLECTION_DETECTED: { msg: 'collectionDetected' },
  CARD_PARSE_FAILED: { msg: 'cardParseFailed' },
  // ── forms（2.4forms-design）──
  UNSUPPORTED_FORM_TEMPLATE_FORMAT: { msg: 'unsupportedFormTemplateFormat' },
  FORM_TEMPLATE_ANALYZING: { msg: 'formTemplateAnalyzing' },
  FORM_TEMPLATE_NOT_READY: { msg: 'formTemplateNotReady' },
  FORM_TEMPLATE_CHANGED: { msg: 'formTemplateChanged' },
  FORM_TEMPLATE_MISSING: { msg: 'formTemplateMissing' },
  FORM_FILL_RUNNING: { msg: 'formFillRunning' },
  FORM_FILL_CONCURRENT_LIMIT: { msg: 'formFillConcurrentLimit' },
  // ── ppt（2.3 §6.3 E2/E3）──
  NOT_ENHANCEABLE: { msg: 'pptNotEnhanceable' },
  SOURCE_NOT_FOUND: { msg: 'pptSourceNotFound' },
  ENHANCE_IN_PROGRESS: { msg: 'pptEnhanceInProgress' },
  ENHANCE_CREATE_FAILED: { msg: 'pptEnhanceCreateFailed' },
  LAUNCH_FAILED: { msg: 'pptLaunchFailed' },
  PPT_NOT_FOUND: { msg: 'pptNotFound' },
  PPT_FINISHED: { msg: 'pptFinished' },
  CREATE_FAILED: { msg: 'pptCreateFailed' },
  UNSUPPORTED_PPTX_FORMAT: { msg: 'pptUnsupportedPptxFormat' },
  INVALID_TEMPLATE_ID: { msg: 'pptInvalidTemplateId' },
  INVALID_TEMPLATE_KIND: { msg: 'pptInvalidTemplateKind' },
  INVALID_TEMPLATE_NAME: { msg: 'pptInvalidTemplateName' },
  INVALID_TEMPLATE_REF: { msg: 'pptInvalidTemplateRef' },
  TEMPLATE_ID_EXISTS: { msg: 'pptTemplateIdExists' },
  TEMPLATE_JOB_ACTIVE: { msg: 'pptTemplateJobActive' },
  TEMPLATE_DELETE_FAILED: { msg: 'pptTemplateDeleteFailed' },
  TEMPLATE_NOT_FOUND: { msg: 'pptTemplateNotFound' },
  BUILTIN_TEMPLATE: { msg: 'pptBuiltinTemplate' },
  SPEC_NOT_FOUND: { msg: 'pptSpecNotFound' },
  LIB_NOT_FOUND: { msg: 'pptLibNotFound' },
  SCRIPT_NOT_FOUND: { msg: 'pptScriptNotFound' },
  // ── InkCubeError（errors.py 落库链路）──
  LLM_NOT_CONFIGURED: { msg: 'llmNotConfigured', sug: 'llmNotConfiguredSug' },
  LLM_INVALID_KEY: { msg: 'llmInvalidKey', sug: 'llmInvalidKeySug' },
  LLM_RATE_LIMITED: { msg: 'llmRateLimited', sug: 'llmRateLimitedSug' },
  LLM_QUOTA_EXHAUSTED: { msg: 'llmQuotaExhausted', sug: 'llmQuotaExhaustedSug' },
  LLM_MODEL_NOT_FOUND: { msg: 'llmModelNotFound', sug: 'llmModelNotFoundSug' },
  LLM_NETWORK_ERROR: { msg: 'llmNetworkError', sug: 'llmNetworkErrorSug' },
  LLM_NETWORK_TIMEOUT: { msg: 'llmNetworkTimeout' },
  LLM_CALL_FAILED: { msg: 'llmCallFailed' },
  EMBEDDING_MODEL_MISSING: { msg: 'embeddingModelMissing', sug: 'embeddingModelMissingSug' },
  PARSING_MODEL_MISSING: { msg: 'parsingModelMissing', sug: 'parsingModelMissingSug' },
  FILE_NOT_FOUND: { msg: 'fileNotFound' },
  SYSTEM_DB_ERROR: { msg: 'systemDbError' },
  SYSTEM_PROCESS_DOWN: { msg: 'systemProcessDown', sug: 'systemProcessDownSug' },
  SYSTEM_START_FAILED: { msg: 'systemStartFailed', sug: 'systemStartFailedSug' },
}

/** Router 同步错误文案：code 命中映射 → 当前语言；否则 fallback（后端原文）。 */
export function apiErrorText(code: string | null | undefined, fallback: string, t: TFn): string {
  const entry = code ? ERROR_CODE_I18N[code] : undefined
  return entry ? t(`errors.codes.${entry.msg}`) : fallback
}

/** Router 同步错误建议文案：code 命中且带 suggestion → 当前语言；否则 fallback。 */
export function apiErrorSuggestion(code: string | null | undefined, fallback: string, t: TFn): string {
  const entry = code ? ERROR_CODE_I18N[code] : undefined
  return entry?.sug ? t(`errors.codes.${entry.sug}`) : fallback
}

/** 落库文本还原：`INKCUBE_CODE:<CODE>|<detail>` → 当前语言文案；
 *  文中不含协议串时原样返回（历史中文数据 / 第三方异常串零成本兼容）。 */
const STORED_CODE_RE = /INKCUBE_CODE:([A-Z0-9_]+)(?:\|([^\n]*))?/g

export function translateStoredError(text: string, t: TFn): string {
  return text.replace(STORED_CODE_RE, (_m, code: string, detail?: string) => {
    const entry = ERROR_CODE_I18N[code]
    if (!entry) return detail ? `${code}: ${detail}` : code
    const msg = t(`errors.codes.${entry.msg}`)
    return detail ? `${msg} (${detail})` : msg
  })
}
