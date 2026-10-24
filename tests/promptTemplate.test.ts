/** tests/promptTemplate.test.ts — 内置模板注册表与占位符解析（原 src/features/task/promptTemplate.test.ts，
 *  vitest include 仅收 tests/**，原位文件从未被执行，E2-T4 迁入并随 resolvePrompt 移除裁剪）。 */
import { describe, it, expect } from 'vitest';
import {
  TASK_TEMPLATES,
  INTEL_TEMPLATES,
  INTEL_CATEGORY_MAP,
  TEMPLATE_CATEGORIES,
  hasUnfilledPlaceholders,
  uniquePlaceholderNames,
  applyValues,
} from '../src/features/task/promptTemplate';

describe('hasUnfilledPlaceholders', () => {
  it('含 {xx} 判真', () => {
    expect(hasUnfilledPlaceholders('a{企业名}b')).toBe(true);
  });
  it('已替换判假', () => {
    expect(hasUnfilledPlaceholders('a华润b')).toBe(false);
  });
  it('空字符串判假', () => {
    expect(hasUnfilledPlaceholders('')).toBe(false);
  });
});

describe('uniquePlaceholderNames', () => {
  it('同名占位符去重、按首次出现顺序保留', () => {
    expect(uniquePlaceholderNames('为「{产业}」编制，检索「{产业} 融资」「{产业} 获投」'))
      .toEqual(['产业']);
  });
  it('多变量各出现一次，全部保留', () => {
    expect(uniquePlaceholderNames('基于「{企业名}」与「{关注点}」'))
      .toEqual(['企业名', '关注点']);
  });
  it('无占位符/空串返回空数组', () => {
    expect(uniquePlaceholderNames('普通文本')).toEqual([]);
    expect(uniquePlaceholderNames('')).toEqual([]);
  });
});

describe('applyValues', () => {
  it('同名占位符全量替换（填一次、全文生效）', () => {
    expect(applyValues('# 「{企业名}」报告\n摸清「{企业名}」底数', { 企业名: '华润' }))
      .toBe('# 「华润」报告\n摸清「华润」底数');
  });
  it('未填变量保留原样', () => {
    expect(applyValues('基于「{企业名}」与「{关注点}」', { 企业名: '华润' }))
      .toBe('基于「华润」与「{关注点}」');
  });
  it('空 values 返回原文', () => {
    const src = 'a{企业名}b';
    expect(applyValues(src, {})).toBe(src);
  });
  it('变量名包含例文符号（原 fileOrganize 写法）也能匹配', () => {
    expect(applyValues('按「{规则}」与「{规则，如：按类型/按日期}」', { 规则: '按类型' }))
      .toBe('按「按类型」与「{规则，如：按类型/按日期}」');
  });
});

describe('TASK_TEMPLATES / INTEL_TEMPLATES', () => {
  it('任务模板 6 条且 id 唯一、分类齐全（V2-E1a T12 收窄）', () => {
    expect(TASK_TEMPLATES).toHaveLength(6);
    expect(new Set(TASK_TEMPLATES.map((t) => t.id)).size).toBe(6);
    expect(new Set(TASK_TEMPLATES.map((t) => t.category))).toEqual(
      new Set(['info', 'rep', 'off']),
    );
  });
  it('订阅白名单 6 条且 id 唯一、分类齐全，含两新政策模板', () => {
    expect(INTEL_TEMPLATES).toHaveLength(6);
    expect(new Set(INTEL_TEMPLATES.map((t) => t.id)).size).toBe(6);
    expect(new Set(INTEL_TEMPLATES.map((t) => t.category))).toEqual(
      new Set(['mon', 'ind', 'reg']),
    );
    const ids = INTEL_TEMPLATES.map((t) => t.id);
    expect(ids).toContain('industryPolicy');
    expect(ids).toContain('regionPolicy');
  });
  it('INTEL_CATEGORY_MAP 覆盖全部分类且值落在订阅产出四分类', () => {
    const cats = TEMPLATE_CATEGORIES.slice(1) as unknown as string[];
    expect(Object.keys(INTEL_CATEGORY_MAP).sort()).toEqual([...cats].sort());
    for (const v of Object.values(INTEL_CATEGORY_MAP)) {
      expect(['corporate', 'industry', 'region', 'general']).toContain(v);
    }
  });
});

describe('TEMPLATE_CATEGORIES', () => {
  it('以 all 开头且覆盖全部分类', () => {
    expect(TEMPLATE_CATEGORIES[0]).toBe('all');
    expect(new Set(TEMPLATE_CATEGORIES.slice(1))).toEqual(
      new Set(['info', 'mon', 'ind', 'reg', 'rep', 'off']),
    );
  });
});
