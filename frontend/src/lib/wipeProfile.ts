/**
 * 「重新从零开始」的通用 helper：
 *   同时清理 4 类用户记忆（后端 2 条 + 本地 2 类前缀 key），
 *   供 PreferenceProfile 画像页 / History 推荐历史页 / 其他入口复用。
 *
 * 4 类清理目标（严格对齐 PreferenceProfile 的 onWipeAll 行为）：
 *   1. 后端：DELETE /preferences → 清空全部画像快照
 *   2. 后端：DELETE /history     → 清空全部推荐历史
 *   3. 本地：eatwhat:questionnaire:draft:v1.0:*  → 4 份问卷草稿（AI / Rule / AI-Optimized / Rule-Fallback）
 *   4. 本地：eatwhat:campaign:hidden-until-ms:*  → 活动横幅"今日不再显示"记忆
 *   5. 本地：eatwhat:history:page                 → 推荐历史页分页/筛选状态（顺带一并清掉）
 *
 * 返回：{ deleted_pref_snapshots, deleted_history }：
 *   - 分别是两条后端 DELETE 的 deleted 条数
 *   - 某一条失败会返回 0（只要不是两条同时失败，helper 继续清本地，保证尽量干净）
 *   - 两条同时失败 → 抛出原始错误，交给调用方弹 error toast
 */

import { api } from '../services/api/client';

export interface WipeResult {
  readonly deleted_pref_snapshots: number;
  readonly deleted_history: number;
}

const DRAFT_PREFIX = 'eatwhat:questionnaire:draft:v1.0:';
const CAMP_HIDE_PREFIX = 'eatwhat:campaign:hidden-until-ms:';
const HISTORY_FILTER_KEY = 'eatwhat:history:page';

export async function wipeAllPreferencesAndHistory(): Promise<WipeResult> {
  // 1) 后端两条 DELETE 并行跑（互相不依赖，用 allSettled 容错）
  const [prefs, history] = await Promise.allSettled([
    api.preferenceDeleteAll(),
    api.historyDeleteAll(),
  ]);
  const deletedPrefs =
    prefs.status === 'fulfilled' ? prefs.value.deleted : 0;
  const deletedHistory =
    history.status === 'fulfilled' ? history.value.deleted : 0;

  if (prefs.status === 'rejected' && history.status === 'rejected') {
    // 两条同时失败 = 网络/后端整体挂了 → 抛错让调用方提示"稍后再试"，本地先不要清（避免状态不一致）
    throw prefs.reason instanceof Error
      ? prefs.reason
      : history.reason instanceof Error
        ? history.reason
        : new Error('画像与推荐历史清空请求同时失败，请稍后再试。');
  }

  // 2) 本地 key 清理（只扫一次 localStorage，避免多次写入触发 storage 事件）
  if (typeof window !== 'undefined') {
    const toRemove: string[] = [];
    for (let i = 0; i < window.localStorage.length; i += 1) {
      const k = window.localStorage.key(i);
      if (!k) continue;
      if (
        k.startsWith(DRAFT_PREFIX) ||
        k.startsWith(CAMP_HIDE_PREFIX) ||
        k === HISTORY_FILTER_KEY
      ) {
        toRemove.push(k);
      }
    }
    toRemove.forEach((k) => window.localStorage.removeItem(k));
  }

  return {
    deleted_pref_snapshots: deletedPrefs,
    deleted_history: deletedHistory,
  };
}

/**
 * 生成 wipe 成功后的 toast 文案，格式与 PreferenceProfile 保持一致。
 */
export function formatWipeSuccessNotice(result: WipeResult): string {
  return (
    `已重新从零开始：` +
    `删除画像 ${result.deleted_pref_snapshots} 条、` +
    `推荐历史 ${result.deleted_history} 条，本地草稿与横幅隐藏记忆已清空。`
  );
}
