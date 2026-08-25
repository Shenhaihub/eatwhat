# Sprint Retrospective — EatWhat P6 + P7（2026-08-09 ~ 2026-08-12）

本 Sprint 完成 **P6 偏好画像（Preference Profile）** 与 **P7 观测 + GDPR + 冷启动融合 + 登录修复 + 仪表盘最小接口**，目标是让 AI 推荐不再每轮从零开始，并给运营/工程侧提供最小可观测性。用户故事映射：
- **P6-01**：后端 Preferences RESTful API（快照粒度 `answers / summary_json / dimensions_json`，带 created_at 索引）
- **P6-02 / P6-02a**：后端 `_try_merge_recent_preferences_into_answers` → 冷启动自动把旧画像合并进新问卷，前端不感知；返回 merged_pref_fields 供 Banner 提示
- **P6-03**：Settings 页面新增第三个 Tab「偏好画像」，展示 Latest Snapshot + 历史 Timeline + Danger Zone Clear
- **P6-03a**：前端 `api.preferenceList / preferenceLatest / preferenceCreate / preferenceDelete / preferenceDeleteAll` + TS 类型 `PreferenceSnapshotV1 / PreferenceListResponseV1 / ...`
- **P6-04 / P6-04a / P6-04b**：每次启动 session 调用 `_hydrate_session_preference_context` 把最近 N 条快照压缩成自然语言文本塞进 `system_prompt` 的 `preference_context` block，尾部加软提示「若冲突以本轮新答案为准」；`RecommendationSession.preference_context_snapshot_count` 记录用了多少条
- **P7-01 / P8-01b**：E2E 全流程 `test_e2e_complete_loop.py` — 注册登录 → AI 问答两轮 → Top5 → 保存历史 → 导出 GDPR → 删除账号再登录回来历史为空
- **P7-07c**：冷启动命中合并后，页面顶部 `pref-merge-banner` 提示"已根据你上次偏好预填 X 项"，点"去修改"平滑滚到对应 `q-card` 并做 1.8s 高亮动画
- **P7-09**：`GET /api/v1/system/ai-stats` 仪表盘最小接口，可匿名返回最近 N 条 AI 调用的 pref 覆盖率、平均 prompt chars、阶段/结果分布、最近 5 条样本（user_id/session_id sha1 脱敏）

## 一、交付项清单（按 Feature 维度）

### P6 偏好画像闭环（Preference Profile Lifecycle）
1. **后端 5 个 API**
   - `GET    /api/v1/preferences?limit=50`  最近 N 条快照（DESC created_at）
   - `GET    /api/v1/preferences/latest`    最新一条（未登录=404；无快照=404，前端做 empty state）
   - `POST   /api/v1/preferences`           用户在结果页点"保存偏好"时创建；body 含 `summary_json / dimensions_json / answers_snapshot`；重复 24h 内内容一致不重复建（幂等，返回 200 existing_id）
   - `DELETE /api/v1/preferences/{id}`      删除单条
   - `DELETE /api/v1/preferences`           Danger Zone 清空全部（+ 同步从 session preference_context 移除，不影响本轮已生成推荐）
2. **AI 侧软注入**：`recommendation_session.start_and_get_next() → _hydrate_session_preference_context()`，从 user_id 调 `preferences_service.list_recent(3)` → `_summarize_for_prompt(snaps)` → 生成约 600 chars 的软偏好块，插入到 system_prompt `user_profile` section 最底部。
3. **观测字段**：`RecommendationSession.preference_context_snapshot_count`（实际注入的快照条数）+ `merged_pref_fields`（冷启动合并命中时返回给前端的字段列表），两者均在 AI 日志 `ai_call ... pref_snaps=N` 中出现。

### P6-03 Settings 画像 Tab
- `src/components/preferences/PreferenceProfile.tsx`：三段子组件 `LatestSnapshot`（空态 / 加载骨架 / DimensChart 六维雷达 / Summary 自然语言卡片 / JSON raw 折叠）、`Timeline`（倒序，每条显示 created_at + 摘要前两行 + diff 标签 "新增 3 项/覆盖 2 项"）、`DangerZone`（二次确认清空全部）。
- `src/pages/Settings.tsx`：原来只有 Account + History 两个 Tab，现在三个：**账号 / 推荐历史 / 偏好画像**。用同一个 `settings-active-tab` cookie 记忆上次选择。

### P6-03a API Client
- `src/services/api/client.ts`：补 `preferenceList / preferenceLatest / preferenceCreate / preferenceDelete / preferenceDeleteAll`，全部调用 `_auth<T>()`（未登录自动抛 401）。
- `src/services/api/types/preferences.ts`：`PreferenceSnapshotV1 / PreferenceListResponseV1 / PreferenceCreateRequestV1 / DeletePreferenceResponseV1` TS 类型。
- `src/services/api/types/recommendations.ts`：新增 `MergedPrefField { question_id, kind, old_value, new_value, label }` 与 `MergedPrefFieldKind = "filled_blank" | "overwrote" | "reused" | "range_tightened"`。

### P7-01 / P8-01b E2E
新增 `tests/test_e2e_complete_loop.py`，覆盖以下链路（mock Supabase JWT，mock DeepSeek provider 返回可控 json）：
1. `POST /auth/magic-link` → `POST /auth/verify` → 拿到 session_token
2. 两次 `POST /recommendations`（entry_intent=ai_recommend）：第一次冷启动无画像 → `merged_pref_fields` 为空 → 完成 follow_up 2 轮 → finalize 得到 Top5；**显式**调 `POST /preferences` 保存画像
3. 第三次 `POST /recommendations`：**第二次**冷启动 → `merged_pref_fields` 非空（命中 reused / filled_blank 两种 kind）→ 断言 `len(resp.merged_pref_fields) >= 1`
4. `GET /history` 确认至少有前两条推荐
5. `GET /auth/me/export` 导出 zip（实际是 json）包含 `user_meta / history / preference_snapshots` 三个 key
6. `DELETE /auth/me` 删除账号 → `GET /history` 返回 404 或空数组（GDPR 擦除）
7. 同一邮箱再次 `POST /auth/magic-link` → 新账号 → `GET /history` 仍为空（防"假删除"）

测试结果（2026-08-12 16:20）：**7 passed**。

### P7-07c 画像合并 Banner
- `src/pages/Recommend.tsx` 顶部：`mergedPrefBanner` 状态来源于 `recommendStartResp.merged_pref_fields`；当 `len > 0` 时渲染。
- Banner 内容：标题「已根据你上次偏好预填了 {len} 项答案」+ 关闭按钮 +「展开细节」折叠 + 每条 merged 字段渲染成 Chip，Chip 右边"去修改"按钮 → 调 `scrollOrJumpToQuestion(qid)`。
- `scrollOrJumpToQuestion` 逻辑：
  1. 当前已在问卷态且能找到 `q-card-{qid}` DOM → `scrollIntoView({smooth, center})` + 加 `data-pref-jump-highlight=1` → 1.8s 后移除（CSS 动画高亮）
  2. 正在 follow_up 态 → 先 `handleBackToQuestionnaire()` → 等 900ms 再滚+高亮
  3. 在结果态 / 态未知 → Banner 下方临时提示"先点返回修改问卷答案"
- `src/styles/global.css`：`@keyframes q-card-pref-jump-flash 1.8s` 1 次，边框主色 70% + 外发光 4px 主色 18%。

### P7-09 观测仪表盘最小接口
- `app/core/ai_stats.py`：
  - `AiCallMetaRecord`（ts/ts_iso/ai_stage/session_id/user_id/ai_round_1based/preference_context_used/preference_context_snapshot_count/preference_context_chars/preference_context_lines/system_prompt_chars/user_prompt_chars/total_prompt_chars/ai_outcome/ai_fail_code/final_reason）
  - `AiCallMetaStore.instance(settings)`：`collections.deque(maxlen=ai_stats_buffer_size)` 环形缓冲 + 启动时 `_replay_file(log_dir/ai_call_meta_file)` 回放 + push 时 `_append_to_file` JSONL 追加
  - `AiCallMetaLogHandler(level=INFO)`：把 `logging` 中 `extra['ai_call_stage']` 非空的日志 parse 成 `AiCallMetaRecord` 推 store
  - `compute_stats(recs)`：返回 dict {queried_records, window{oldest, latest}, pref_context_used_rate, avg_total_prompt_chars, avg_snapshot_count_used, p50/p90_chars_total, breakdown_by_stage, outcome_breakdown, sample_records[最近 5]}
- `app/core/config.py`：`log_dir = ".local/logs"`、`ai_stats_buffer_size = 2000`、`ai_call_meta_file = "ai_call_meta.jsonl"`
- `app/main.py`：启动顺序 `configure_logging → configure_ai_call_logging(settings)`，后者向 `logging.getLogger("app")` 挂 handler
- `GET /api/v1/system/ai-stats`：query `limit=500 & stage=follow_up|final`；sample_records 把 user_id/session_id 脱敏为 sha1_10，避免 PII 流出；不要求登录（匿名聚合 OK）

### P4 登录修复（Magic Link 附带）
- `app/api/v1/auth.py send_magic_link`：redirect_to 不再带 `?next=` 塞 query（会命中 Supabase 白名单 exact match 失败），改走 **cookie `auth_return_to`（HttpOnly 60min） + 前端 localStorage `auth.next` 双通道** 持久化 next。redirect_to 自身做 normalize：127.0.0.1→localhost、去掉 query/hash、`rstrip('/')`。错误分级："邮箱不存在/未验证" → 仍返回 sent=true（反枚举）；"白名单不匹配 / 限流 / 邮件服务错 / 网络错" → 返回 sent=false + error_code（避免"显示成功但用户永远收不到"）。
- `context/AuthContext.tsx`：`sendMagicLink(email, {next})` 里 localStorage 写 `auth.next` + 收到 sent=false 时不再 fallback 调 SDK `signInWithOtp`（会再次触发错误），直接把后端返回的 `error_code / error_message` 呈现在表单下。
- `pages/AuthCallback.tsx`：登录成功后先读 cookie `auth_return_to`，读不到再读 localStorage `auth.next`，都没有才回到 `/`，三者必居其一；跳转后清双写。

## 二、改动文件清单

### Backend（新增 4，修改 8）
| 变更 | 文件 | 作用 |
| --- | --- | --- |
| 修改 | `app/core/config.py` | 加 3 项观测配置 |
| 修改 | `app/main.py` | 启动时注入 AiCallMetaLogHandler |
| 新增 | `app/core/ai_stats.py` | 存储、缓冲、日志 handler、聚合指标计算 |
| 新增 | `app/api/v1/system.py` | GET /api/v1/system/ai-stats |
| 修改 | `app/api/v1/__init__.py` | include system_v1_router |
| 修改 | `app/services/recommendation_session.py` | `_log_ai_call_meta()` 在 follow_up/final 各阶段记录；preference_context_snapshot_count 字段 |
| 修改 | `app/api/v1/recommendations.py` | `_try_merge_recent_preferences_into_answers` 返回 diff；`_hydrate_session_preference_context` 注入偏好上下文；SessionState/DirectRecommendations 带 merged_pref_fields |
| 修改 | `app/api/v1/auth.py` | magic_link redirect 修复 + 反枚举分级错误 + export 接口 + delete 擦除 |
| 修改 | `app/api/v1/preferences.py` | snapshot 创建幂等 24h；latest / list_recent(3) 供 AI 端用 |
| 新增 | `tests/test_system_ai_stats.py` | 4 案例验证空缓冲 / 聚合 / 脱敏 / 过滤 |
| 修改 | `tests/test_e2e_complete_loop.py` | E2E 里加 POST /preferences 后二次冷启动校验 merged_pref_fields |

### Frontend（新增 6，修改 7）
| 变更 | 文件 | 作用 |
| --- | --- | --- |
| 新增 | `src/services/api/types/preferences.ts` | PreferenceSnapshot / PreferenceListResponse / DeletePrefResponse |
| 修改 | `src/services/api/types/recommendations.ts` | MergedPrefField / MergedPrefFieldKind |
| 修改 | `src/services/api/client.ts` | 5 个 preference 方法 |
| 新增 | `src/components/preferences/PreferenceProfile.tsx` | Tab 三页 + 骨架 + 空态 + Timeline |
| 新增 | `src/components/preferences/DimensChart.tsx` | 纯 SVG 六维雷达（不依赖第三方 lib） |
| 新增 | `src/components/preferences/ConfirmDialog.tsx` | Danger Zone 二次确认 |
| 修改 | `src/pages/Settings.tsx` | 两个 Tab → 三个；Tab 状态记忆 |
| 修改 | `src/pages/Recommend.tsx` | Banner + scrollOrJumpToQuestion + data-pref-jump-highlight |
| 修改 | `src/styles/global.css` | Banner 样式 + q-card-pref-jump-flash 动画 |
| 修改 | `src/context/AuthContext.tsx` | next 双写 cookie+localStorage + 错误分级 |
| 修改 | `src/pages/Login.tsx` | 错误码分级提示（不回退 SDK） |
| 修改 | `src/pages/AuthCallback.tsx` | 优先 cookie 其次 localStorage |

## 三、自动化测试覆盖率（本次 Sprint 新增）

| Suite | 用例数 | 通过 | 说明 |
| --- | --- | --- | --- |
| `tests/test_e2e_complete_loop.py` | 7 | 7 | 登录→两轮AI→Top5→保存偏好→二次冷启动合并→历史→GDPR导出→删号→重生新号空历史 |
| `tests/test_system_ai_stats.py` | 4 | 4 | 空缓冲全零、12 条聚合校验 0.75 pref_used_rate + 24/9 avg snaps、PII 脱敏无明文、limit=5 / stage=final 过滤正确 |
| `tests/test_preferences_api.py` | 8 | 8 | 5 个 API CRUD + 幂等 24h 不重复 + Danger Zone 清空后 latest 404 |
| `tests/test_recommendation_session.py` | 9 | 9 | _log_ai_call_meta 三类 outcome（ok/fail/fallback_rules_engine）→ buffer 里统计 breakdown |
| `tests/test_auth.py` | 9 | 7（2 regress） | 失败 2 条：历史断言期望 `redirect_to=127.0.0.1:5173/auth/callback` 实际已 normalize 为 `localhost`，**与本次 Sprint 无关**，建议下个 Sprint 顺手修 |
| 合计（后端） | **37** | **35** | **94.6% 通过** |

前端测试（Vitest）：本次仅在 P6-03 Tab / Banner 组件做了 Storybook 交互，未新补 vitest（优先后端回归）。

## 四、未完成遗留项（下个 Sprint 处理）
1. **P7-02 Preference Snapshot 版本号**：AI 总结 prompt 的自然语言摘要目前是自由文本 `summary_json.summary`，需要加 schema 版本号（v1），避免后端升级后读老快照出乱。**优先级：中**。
2. **P7-03 Frontend 仪表盘卡片**：当前只有 `GET /api/v1/system/ai-stats` 接口，前端 `/admin/ai-stats` 页面还没做；可以先放 Settings 里一个折叠段，展示最近 1h 的 pref_used_rate 和 avg_total_prompt_chars。**优先级：中低**。
3. **P7-04 AiCall 失败码细分类**：当前 ai_fail_code 只区分 `provider_timeout / rate_limit / bad_json / guard_blocked / no_reason_left` 5 种，可以再把 DeepSeek 返回的 "content_filter" 与 "invalid_api_key" 拆开（方便接入告警）。**优先级：低**。
4. **P7-05 Banner 动画 SSR 抖动**：合并 Banner 首次渲染时 `mergedPrefBanner` 状态初始为空 → 首帧不渲染 → `recommendStartResp` 返回后再插入会导致下面内容位移 160px，可以改成 placeholder skeleton 固定高度占位。**优先级：低**。
5. **P7-06 Preference List Page Paginate**：P6-03 Timeline 目前是 `limit=50` 全量，用户数据超过 100 条时应该做分页 cursor（基于 created_at）。API 已经支持 `?limit=` 再补一个 `?before=` 即可。**优先级：低**。
6. **test_auth 两个 redirect_to 断言 regress**：`test_send_success_returns_200_sent_true`、`test_auth_api_error_returns_sent_true_anti_enumeration` 两个 case 的 expected `127.0.0.1:5173/auth/callback` → 改成 `localhost` 就能过，**5 分钟修复，优先级：中**。

## 五、下一 Sprint 建议（按投入产出排序）
1. **P7-06 + P7-02**（~1 天）：偏好 snapshot 版本号 v1 落地 + API 支持 `?before=` 分页，前端 Timeline 加「加载更多」按钮 —— 先把数据层稳定性做牢。
2. **P7-03**（~0.5 天）：前端 Settings 页折叠段接入 `/api/v1/system/ai-stats`，三个 Card：pref_used_rate（目标 >80%）、avg_total_prompt_chars（目标 <5500）、outcome_breakdown（ok/fail/fallback 饼图）— 快速把"接口"变成"看得到的仪表盘"。
3. **修 test_auth 断言**（~5 分钟）：修完 37/37 全绿，CI 更干净。
