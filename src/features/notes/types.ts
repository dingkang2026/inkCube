/** src/features/notes/types.ts — 会议/拜访纪要类型（2.13 §3/§4）。
 *
 * MeetingNote 与 python meeting_notes 表行（db._decode_meeting_note 输出）对齐；
 * detectTranscript 为输入步转写稿检测纯函数（轻量行首模式匹配，2.13 §3 免培训）。
 */

/** 纪要行状态（四态状态机，2.14）：draft 输入中 / structuring LLM 整理中 /
 * structured 待确认 / archived 已归档（confirm 完成终态，归档列表数据源） */
export type MeetingNoteStatus = 'draft' | 'structuring' | 'structured' | 'archived'

/** 结构化失败错误码（meeting_notes_service 落库值；i18n meetingNotes.error.* 映射） */
export type MeetingNoteErrorCode =
  | 'LLM_BAD_RESPONSE' | 'LLM_FAILED' | 'NO_MATERIAL'
  | 'IMAGE_FAILED' | 'IMAGES_TIMEOUT'

export interface MeetingNote {
  id: string
  project_id: string
  title: string
  occurred_at: string | null
  raw_text: string | null
  /** 关联素材图片 file id 列表（解析产物 images.vlm_description 进材料） */
  image_file_ids: string[]
  status: MeetingNoteStatus
  /** 结构化产物 JSON：{content_md, action_items:[{title}]} */
  draft_md: string | null
  /** confirm 分步幂等游标 JSON：{file_id, task_ids:number[]}（后端消费） */
  saved_refs: string | null
  last_error: MeetingNoteErrorCode | null
  created_at: string
  updated_at: string
}

/** draft_md 解析形态（向导确认步编辑模型；缺字段容错由消费方兜底空值） */
export interface NoteDraftPayload {
  content_md: string
  action_items: { title: string }[]
}

/** 确认步行动项编辑行：勾选（建事务）/ 标题可改 / 可选截止日 */
export interface ConfirmActionItem {
  title: string
  selected: boolean
  dueDate: string | null
}

const SPEAKER_LINE = /^\s*(?:发言人[^\s:：]{0,10}|说话人\s*\d*|[\u4e00-\u9fa5A-Za-z0-9]{2,4})[:：]/
const TIMESTAMP_LINE = /^\s*\[\d{1,2}:\d{2}(?::\d{2})?\]/

/** 转写稿检测（2.13 §3）：行首「发言人/说话人N/姓名：」或「[MM:SS」时间戳
 *  命中 ≥2 行即判定。轻量启发不阻断——仅提示「将按对话结构提取行动项」。 */
export function detectTranscript(text: string): boolean {
  let hits = 0
  for (const line of text.split('\n')) {
    if (!line.trim()) continue
    if (SPEAKER_LINE.test(line) || TIMESTAMP_LINE.test(line)) hits++
    if (hits >= 2) return true
  }
  return false
}
