/** src/features/onboarding/finishNav.ts — Onboarding 完成后的导航决策(0.5 §4.5 E2 T8)。
 *  零项目完成向导 → 导航 projects:项目欢迎页(ProjectWelcomeGuide)三步引导自然呈现,
 *  引导卡即引导,不新增气泡层;已有项目则停留在当前页。 */
export function navAfterOnboarding(projectCount: number): 'projects' | null {
  return projectCount === 0 ? 'projects' : null
}
