/** tests/promptTemplate.test.ts — 内置模板注册表与占位符解析（原 src/features/task/promptTemplate.test.ts，
 *  vitest include 仅收 tests/**，原位文件从未被执行，E2-T4 迁入并随 resolvePrompt 移除裁剪）。 */
import { describe, it, expect } from 'vitest';
import {
  TASK_TEMPLATES,
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

describe('TASK_TEMPLATES', () => {
  it('恰好 10 条且 id 唯一、分类齐全', () => {
    expect(TASK_TEMPLATES).toHaveLength(10);
    expect(new Set(TASK_TEMPLATES.map((t) => t.id)).size).toBe(10);
    expect(new Set(TASK_TEMPLATES.map((t) => t.category))).toEqual(
      new Set(['info', 'mon', 'ind', 'rep', 'off']),
    );
  });
});

describe('TEMPLATE_CATEGORIES', () => {
  it('以 all 开头且覆盖全部分类', () => {
    expect(TEMPLATE_CATEGORIES[0]).toBe('all');
    expect(new Set(TEMPLATE_CATEGORIES.slice(1))).toEqual(
      new Set(['info', 'mon', 'ind', 'rep', 'off']),
    );
  });
});
