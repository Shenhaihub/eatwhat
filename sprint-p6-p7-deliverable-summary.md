# Sprint 交付清单：P6（画像可视化/API/AI注入）+ P7（可观测/GDPR/冷启动合并）

- 生成时间：2026-08-12
- 范围：P6-03 / P6-03a / P6-04 / P7-05 / P7-06 / P7-07a / P7-07b
- 验收基线：后端 45/45 tests 通过、前端 `npx tsc --noEmit` 0 错误
- 关联 E2E：`backend/tests/test_e2e_complete_loop.py::test_p7_01_e2e_complete_gdpr_loop`

---

## 1. 交付项总览

| 编号 | 名称 | 类型 | 状态 |
|---|---|---|---|
| P6-03 | 偏好画像可视化（设置页 Tab：最新快照 + 时间线 + 危险区） | 前端 UI + 组件 | ✅ |
| P6-03a | 偏好 API 客户端封装（list/latest/create/delete/clearAll + 类型） | 前端服务层 | ✅ |
| P6-04 | 偏好快照 → DeepSeek Prompt 注入（会话 + 直连生成两条路径） | 后端 AI Prompt 拼接 | ✅ |
| P7-05 | AI 调用可观测埋点（pref_used / 快照数 / prompt 字符数 / 阶段） | 后端日志埋点 | ✅ |
| P7-06 | GDPR 数据导出 `GET /api/v1/auth/me/export`（结构化 JSON） | 后端 HTTP 接口 | ✅ |
| P7-07a | 冷启动画像合并后端：返回 `merged_pref_fields`（含改了哪些 qid / 原值 / 新值 / kind） | 后端 HTTP 接口（推荐直连+会话两条） | ✅ |
| P7-07b | 冷启动画像合并前端：/recommend 顶部"已预填 X 项"可展开 Banner（可 dismiss、可看明细） | 前端 UI + 双分支响应体兼容 | ✅ |
| P7-08 | CHANGELOG / Sprint 记录（本文件） | 文档 | ✅ |

---

## 2. 分项详情

### 2.1 P6-03 偏好画像可视化（Settings / Tab3）

**目标**：用户在设置页能看到「偏好画像」Tab，展示最新快照、历史时间线、危险区清空操作。

**关键文件**：
- 前端 [frontend/src/pages/Settings.tsx](file:///d:/A622/项目/AgentWork/project0717/frontend/src/pages/Settings.tsx)：三 Tab 切换（Account / History / Preference Profile）
- 组件 [frontend/src/components/PreferenceProfile.tsx](file:///d:/A622/项目/AgentWork/project0717/frontend/src/components/PreferenceProfile.tsx)
  - 子组件：`PreferenceEmpty`（空状态）、`PreferenceLoading`（骨架）、`PreferenceLatestSnapshotCard`（最新快照卡 + 七维 SVG 雷达图 + follow-up keys 展示）、`PreferenceTimelineSection`（历史时间线 + 单项删除）、`PreferenceDangerZone`（清空全部，二次确认 + 输入校验）
- 样式：[frontend/src/styles/global.css](file:///d:/A622/项目/AgentWork/project0717/frontend/src/styles/global.css) `.pref-*` 类体系

**UI 要点**：
- 最新快照卡：基于"信息丰富度"把七维值映射到 0–100 分数 → SVG 雷达图（无第三方依赖）
- 时间线：dot 标记 + `最新`小徽标 + 每卡带「删除该快照」
- 危险区：需用户输入 `clear all my preference snapshots` 才解锁删除按钮（防误删）

---

### 2.2 P6-03a 偏好 API 客户端封装

**目标**：前端通过强类型 API 客户端与 P6-02 偏好画像 HTTP 接口交互。

**关键文件**：
- 类型：[frontend/src/services/api/types/preferences.ts](file:///d:/A622/项目/AgentWork/project0717/frontend/src/services/api/types/preferences.ts)：`PreferenceSnapshotV1`, `PreferenceSnapshotWriteRequestV1`, `PreferenceListResponseV1`, `PreferenceDeleteResponseV1`, `SevenDimensionValuesV1`, `AiFollowUpKvV1`, `PreferSourceTag` 等
- 导出：[frontend/src/services/api/types/index.ts](file:///d:/A622/项目/AgentWork/project0717/frontend/src/services/api/types/index.ts)
- 客户端方法：[frontend/src/services/api/client.ts](file:///d:/A622/项目/AgentWork/project0717/frontend/src/services/api/client.ts)（`preferenceList`, `preferenceLatest`, `preferenceCreate`, `preferenceDelete`, `preferenceDeleteAll`）

**API 签名**：
```
preferenceList(params?: { limit?: number; offset?: number }): Promise<PreferenceListResponseV1>
preferenceLatest(): Promise<PreferenceSnapshotV1 | null>
preferenceCreate(req: PreferenceSnapshotWriteRequestV1): Promise<PreferenceSnapshotV1>
preferenceDelete(snapshotId: string): Promise<PreferenceDeleteResponseV1>
preferenceDeleteAll(): Promise<PreferenceDeleteResponseV1>
```

---

### 2.3 P6-04 偏好快照 → DeepSeek Prompt 注入

**目标**：生成推荐前加载最近 5 条（可配置）偏好快照，**用规则拼写成自然语言偏好汇总块**注入到 AI prompt；会话失败回落到确定性规则引擎的路径不使用（规则引擎已有七维评分）。

**关键文件**：
- 核心：[backend/app/services/recommendation_session.py](file:///d:/A622/项目/AgentWork/project0717/backend/app/services/recommendation_session.py)
  - `RecommendationSessionManager.start_and_get_next()` 入口加载
  - 辅助函数：`_load_recent_preference_context`（取快照）、`_summarize_preferences_for_prompt`（规则拼自然语言摘要块）
- 直连生成分支：[backend/app/api/v1/recommendations.py](file:///d:/A622/项目/AgentWork/project0717/backend/app/api/v1/recommendations.py) `POST /api/v1/recommendations` 内部 `_try_generate_ai_candidates` 也会做同样的 prompt 注入

**摘要块规则（节选）**：
- 七维 scalar：`[meal_period]: lunch`
- follow_up dict：`- 口味偏辣（程度 8/10，回答：high_spicy）` 这类人类可读句式
- 来源标记：每个维度/字段带来源 tag（`rule_engine` / `ai_extract_rule_based` / `ai_extract_llm_guided`）

---

### 2.4 P7-05 AI 调用可观测埋点

**目标**：每次 AI 调用（follow-up 阶段 / final 阶段）在结构化日志里埋：是否用到 pref 上下文、快照条数、prompt 字符数、outcome/fail_code，方便监控画像注入实际覆盖率。

**关键文件**：
- [backend/app/services/recommendation_session.py](file:///d:/A622/项目/AgentWork/project0717/backend/app/services/recommendation_session.py) 新增 `_log_ai_call_meta()` 方法，在三处调用：
  - `start_and_get_next`（round=1 follow-up 生成）
  - `answer_and_advance`（round=2..N follow-up 生成）
  - `try_ai_finalize_recommendation`（最终 Top5 生成）

**结构化 log extra 字段（log.info 的 extra=... 打入 JSON 日志）**：
```
ai_call_stage            : "follow_up" | "final"
session_id / user_id     : 会话/用户定位
ai_round_1based          : 第几轮（final 可空）
preference_context_used  : bool
preference_context_snapshot_count : 最近 N 条中实际使用数
preference_context_chars / lines   : pref block 体积
system_prompt_chars / user_prompt_chars / total_prompt_chars
ai_outcome               : "success" | "fail_fallback_rule" | "timeout_fallback_rule" | ...
ai_fail_code / final_reason        : 失败/最终来源细分
```

---

### 2.5 P7-06 GDPR 数据导出

**目标**：已登录用户可通过接口**一次性导出全部个人数据**（结构化 JSON、UTF-8 BOMless、可被通用工具读取）。

**关键文件**：
- [backend/app/api/v1/auth.py](file:///d:/A622/项目/AgentWork/project0717/backend/app/api/v1/auth.py) 新增：
  - `GET /api/v1/auth/me/export` → `UserDataExportResponseV1`

**响应体结构**：
```jsonc
{
  "exported_at": "ISO8601 UTC",
  "schema_version": "gdpr-portability-v1",
  "user_meta": { "user_id", "email"?, "created_at"?, "last_login_at"? },
  "recommendation_history": [ /* RecommendationHistoryResponseItemV1[] */ ],
  "preference_snapshots":   [ /* PreferenceSnapshotV1[]（完整快照，含七维/follow-up/source） */ ]
}
```
- 注意：与 `DELETE /api/v1/auth/me` 删除顺序互补——删账号会级联删除 history、snapshots、session。

---

### 2.6 P7-07a 冷启动画像合并后端

**目标**：**新一轮推荐问卷刚进来/答案很空**时，把历史画像（最近 3 条偏好快照）里的有效字段**合并进本次 answers_by_question_id**，合并前/后的值通过 `merged_pref_fields` 返回给前端渲染 banner。

**关键文件**：
- [backend/app/api/v1/recommendations.py](file:///d:/A622/项目/AgentWork/project0717/backend/app/api/v1/recommendations.py)
  - `_try_merge_recent_preferences_into_answers()` 新函数：按 qid 合并，返回 `list[MergedPrefField]`（命中才非空；没有画像、没有新增字段 → 空数组，前端不弹 banner）
  - 在 **两个入口** 都调用：
    - `POST /api/v1/recommendations`（直连生成）→ `RecommendationsGenerateResponseV1.merged_pref_fields`
    - `POST /api/v1/recommendations/session/start`（动态会话启动）→ `SessionStateResponseV1.merged_pref_fields`
  - **语义**：只有 "before ≠ after 且实际改了内容" 才会入列表；无新增字段 → 不浪费用户心智。

**MergedPrefField 形状**：
```python
{
  "qid": "q_taste_preference",
  "question_title": "口味偏好" | None,     # 若题库里能查到就填，方便前端展示
  "kind": "list_append" | "scalar_override" | "ai_follow_up",
  "before_value": <原 answers 里该 qid 的值>,   # 空表示之前没填
  "after_value":  <合并后的值>                 # 保证可序列化、与 answers schema 对齐
}
```

---

### 2.7 P7-07b 冷启动画像合并前端 Banner

**目标**：`/recommend` 页在返回 `merged_pref_fields`（非空）时，**顶部醒目 banner** 告知用户"已基于历史画像自动预填 X 项（可手动修改）"，并支持：折叠/展开明细、关闭（dismiss，本次流程不再展示）、来源标注（AI 动态会话 / 快速规则引擎）。

**关键文件**：
- [frontend/src/pages/Recommend.tsx](file:///d:/A622/项目/AgentWork/project0717/frontend/src/pages/Recommend.tsx)
  - 新增 `MergedPrefField` 类型导入
  - 新增 state：`mergedPrefBanner`（`{ merged, from, dismissed }`） + `prefBannerOpen`（展开/折叠）
  - `generateRecommendations()` 双分支（session/start + fallback POST /recommendations）都会读取 banner 数据，并且做**响应体兼容**（新格式 `{ items, merged_pref_fields }` 或降级的纯数组都能工作）
  - 渲染：`<h1>` 之后插入 banner JSX
- 样式：[frontend/src/styles/global.css](file:///d:/A622/项目/AgentWork/project0717/frontend/src/styles/global.css) `.pref-merge-banner*` + `.chip*` 胶囊体系

**UX 细节**：
- 桌面三段式对比：`合并前（灰 chip） → 合并后（蓝 chip）`，移动端自动竖排 + 箭头旋转 90°
- 三种 kind 用不同颜色胶囊：新增选项（绿）/ 覆盖填充（蓝）/ AI 追问新增（紫）
- 合并前空值用"（未填）"斜体占位，避免用户看不懂"→ 是新增还是覆盖"

---

## 3. 验收命令（快速回归）

```powershell
# 前端：类型检查
cd frontend
npx tsc --noEmit
# 期望：退出码 0，无错误输出

# 后端：核心测试套件（推荐 / 会话 / 画像 / 鉴权 / E2E GDPR 全链路）
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_e2e_complete_loop.py backend/tests/test_api_recommendations_generate.py backend/tests/test_recommendation_session.py backend/tests/test_preferences_api.py backend/tests/test_auth.py -v --no-header
# 期望：45 passed（含 E2E GDPR 全流程）
```

---

## 4. 风险点与回滚说明

### 4.1 主要风险

1. **P6-04 Prompt 体积膨胀**：`preference_context` 可能让 total prompt 字符数上涨 ~2–4k。**缓解**：P7-05 已埋 `total_prompt_chars`，上线后第一周盯日志，必要时把快照条数从 5 调到 3。
2. **P7-07a 合并"打扰老用户"**：老用户每次进入都看到 banner。**缓解**：只在"before ≠ after 且实际新增字段 ≥1"时才返回非空数组；且用户可一键 dismiss。
3. **响应体格式切换（P2 兜底从数组 → 对象）**：历史缓存/网关 mock 可能出错。**缓解**：P7-07b 前端做了"数组 or 对象"双兼容，同时 P7-07a 后端是**非破坏性**的（旧客户端若把 `{ items, merged_pref_fields }` 当成数组用会出错，需要同步升级前端到当前版本；这里 P7-07b 已做，所以只要部署顺序是后端→前端即可）。

### 4.2 回滚方案（若线上异常）

- **只回滚前端**：退回到 P7-07b 之前的版本 → Banner 不会显示（不影响后端返回数据，但 merged_pref_fields 对老前端是未定义字段，会被 TS 忽略，数据不会错）
- **只回滚后端 P6-04/P7-07a**：把 `_try_merge_recent_preferences_into_answers` 改成直接返回 `([] , answers)` 空操作；AI prompt 注入通过 manager 构造时传 `max_snapshots=0` 关掉

---

## 5. 接下来可接续（待用户说顺序）

优先级从高到低：
1. P7-07c：Banner 明细每项加"去修改 →"按钮，点击滚动到对应问题卡片（DOM id + 平滑滚动）
2. P8-01：本地浏览器验收（backend uvicorn + frontend vite），验证 Banner/画像 Tab 全流程
3. P6-04b（可选）：给 DeepSeek prompt 注入里增加"用户可随时推翻历史画像"的自然语言提示词，让模型不会因为历史画像太重而忽略当下最新选择
4. P7-09：把 P7-05 的结构化日志打到专用日志索引（或 ELK/APM 仪表盘），出 pref_used 覆盖率日报
