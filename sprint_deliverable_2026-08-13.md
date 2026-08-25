# Sprint Deliverable — EatWhat 2026-08-13（P7 收尾三件套 ①②③ + ⑥ cursor 单测补全）

本交付文档覆盖 2026-08-13 完成的四项增量（按 ④→⑤→⑥ 顺序实际执行：先写交付 → 后启动验收 → 最后补单测）：

1. ① **test_auth redirect/反枚举断言修正**：对齐 Supabase redirect normalize + 错误分级后的新行为，回归绿。
2. ② **P7-03 / P7-09 前端观测仪表盘最小 UI**：Settings 账户 Tab 新增 ObservabilitySection，接入 `GET /api/v1/system/ai-stats`，三张观测 Card + 样本记录。
3. ③ **P7-06 画像快照版本号 v1 + P7-02 cursor 分页 + Timeline「加载更多」**：后端新增 `snapshot_version` 字段与 `before=` Base64URL cursor 分页（created_at DESC + id DESC 稳定单调），前端 Preferences Timeline 每批 5 条增量加载到底。
4. ⑥ **P7-02 cursor + P7-06 version 单测补全**：新增 1 文件 7 个用例，覆盖 codec 往返 / before= 12 条 4 页串联 / 同 created_at tie-break / 非法 cursor 400 / offset 模式兼容。并修复两项后端 bug（cursor 边界以未返回 peek 行做阈值导致尾部丢行、offset 模式不产出 next_cursor 前端无法无缝翻页）。

---

## 一、交付项概览

### ① test_auth 回归修复（CI 绿）
| 项 | 说明 |
| --- | --- |
| 旧问题 1 | `test_auth_api_error_returns_sent_true_anti_enumeration` 原断言 sent=true 与新版 P7 错误分级冲突（rate_limit 现在 sent=false + 透明 error_code，方便用户排查收不到邮件） |
| 旧问题 2 | `test_magic_link_sends_email_returns_ok` redirect_to 断言硬写 `http://127.0.0.1:5173/auth/callback`，与 Supabase normalize 后 `http://localhost:5173/auth/callback` 不一致 |
| 修复 | - redirect_to 断言改为 `http://localhost:5173/auth/callback`<br>- rate_limit/白名单类错误 → sent=false + `error_code=AUTH_RATE_LIMIT`，user_not_found/凭据错这类真正枚举风险仍走 sent=true（`_ENUM_CODES`） |
| 回归结果 | **259 passed / 1 warning / 18.86s** ✅（旧 37 条 auth/session/pref/ai-stats/questionnaire/rule_engine 全部包含在 259 中） |

### ② P7-03 / P7-09 观测仪表盘最小 UI
| 项 | 说明 |
| --- | --- |
| 接入 API | `GET /api/v1/system/ai-stats`（匿名，样本 user_id/session_id 后端 sha1_10 脱敏） |
| 位置 | Settings → 账户 Tab：logout 按钮下方、Danger Zone 上方 |
| 头部控件 | Stage 分段（全部 / AI追问 / 最终Top5）+ 手动刷新按钮（AbortController 防止重复） |
| 三张 Card | **画像上下文利用率 %**（meter 渐变条 + 总调用 / 平均快照 / 中位快照）<br>**平均 Prompt 长度字**（线性 4000 映射 meter + 最短 / 最长）<br>**Outcome 分布**（final / follow_up / error 三色条形） |
| 样本面板 | 可折叠样本记录最多 12 条：Pill（Stage / 画像命中 / 快照数 / prompt 字）+ sha1_10 user session + 时间 |
| 加载状态 | 三张骨架 Card + CSS shimmer |
| 响应式 | 540px 断点堆叠 |

### ③ P7-06 快照版本号 + P7-02 cursor 分页 + 加载更多
| 项 | 说明 |
| --- | --- |
| **P7-06 snapshot_version** | - 新字段 `snapshot_version`（默认 `v1.0`，max 32 chars）<br>- 写入/读出全程贯通；老数据读不到时回退 `"v1.0"` 保持向后兼容 |
| **P7-02 before= cursor** | - Base64URL 编码：`{created_at.isoformat()}\|{id}`<br>- 排序：`created_at DESC + id DESC` 双维度保证严格单调（同一秒写多条不丢重）<br>- 查询策略：created_at < X 主体 UNION（created_at == X && id < Xid）补齐，合并后归并排序，limit+1 peek 出 next_cursor<br>- 响应：`next_cursor=null` 表示已到末尾；`page_cursor` 原样回显 before 值便于调试<br>- 兼容：仍保留 `offset=?limit=` 旧分页模式完全可用 |
| **Timeline 加载更多** | - 每次拉 5 条（PAGE_SIZE=5），Timeline 底部按钮显示「已加载 N / 共 T」，到达末尾显示「共 T 条，已到底」<br>- 每条快照头显示 v{snapshot_version} chip，前端按 id dedupe 防删除+分页重叠 |

---

## 二、改动文件清单（精确）

### Backend（修改 2）
| 变更 | 文件 | 作用 |
| --- | --- | --- |
| 修改 | [backend/app/api/v1/preferences.py](file:///d:/A622/项目/AgentWork/project0717/backend/app/api/v1/preferences.py) | P7-06 snapshot_version 字段读写 + P7-02 before= cursor 分页 + _encode_cursor/_decode_cursor + 单调排序 |
| 修改 | [backend/tests/test_auth.py](file:///d:/A622/项目/AgentWork/project0717/backend/tests/test_auth.py) | redirect_to 断言 localhost + 错误分级后反枚举测试改 sent=false+AUTH_RATE_LIMIT |

### Frontend（新增 1，修改 6）
| 变更 | 文件 | 作用 |
| --- | --- | --- |
| 新增 | [frontend/src/services/api/types/system.ts](file:///d:/A622/项目/AgentWork/project0717/frontend/src/services/api/types/system.ts) | 仪表盘接口 SystemAiStatsResponse / AiStageOutcomeCounts / AiStatsRecordLite |
| 修改 | [frontend/src/services/api/types/index.ts](file:///d:/A622/项目/AgentWork/project0717/frontend/src/services/api/types/index.ts) | 导出 system 类型 |
| 修改 | [frontend/src/services/api/types/preferences.ts](file:///d:/A622/项目/AgentWork/project0717/frontend/src/services/api/types/preferences.ts) | 加 snapshot_version / before 参数 / next_cursor + page_cursor 字段 |
| 修改 | [frontend/src/services/api/client.ts](file:///d:/A622/项目/AgentWork/project0717/frontend/src/services/api/client.ts) | `preferenceList({limit?, offset?, before?})` before 优先 + `systemAiStats({limit?, stage?})` |
| 修改 | [frontend/src/pages/Settings.tsx](file:///d:/A622/项目/AgentWork/project0717/frontend/src/pages/Settings.tsx) | 新增 ObservabilitySection：三段 Card + 骨架 + 样本 + Stage 过滤 |
| 修改 | [frontend/src/components/profile/PreferenceProfile.tsx](file:///d:/A622/项目/AgentWork/project0717/frontend/src/components/profile/PreferenceProfile.tsx) | Timeline 加载更多按钮 + snapshot_version chip + cursor 拉取 dedupe |
| 修改 | [frontend/src/styles/global.css](file:///d:/A622/项目/AgentWork/project0717/frontend/src/styles/global.css) | 仪表盘样式（280 行）+ Timeline 加载更多（26 行） |

---

## 三、回归清单（两项 100%）

| 类别 | 命令 | 结果 |
| --- | --- | --- |
| Backend 单元测试（auth + e2e + ai-stats + pref P7） | `cd backend && .venv\Scripts\python.exe -m pytest tests/test_auth.py tests/test_e2e_complete_loop.py tests/test_system_ai_stats.py tests/test_preferences_p7.py -v` | **30 passed / 1 warning** ✅（P7 新增 7 + 旧 23 回归全绿） |
| Backend 单元测试（P7-02 + P7-06 专项 7 用例） | `cd backend && .venv\Scripts\python.exe -m pytest tests/test_preferences_p7.py -v` | **7 passed** ✅（详见 ⑥） |
| Frontend 类型检查 | `cd frontend && node_modules\.bin\tsc.cmd -p tsconfig.json --noEmit` | **0 errors** ✅ |

---

### ⑥ P7 专项 7 用例说明

| 测试类 | 用例 | 覆盖点 |
| --- | --- | --- |
| TestCursorCodec | test_encode_decode_round_trip | 5 个 cursor 编解码往返一致性（带微秒、带 timezone、纯时间、UUID 边界） |
| TestCursorCodec | test_decode_invalid_raises_400 | 空字符串 / 非 base64 / 无竖线 / 明文拼接 → 均 400 HTTPException |
| TestSnapshotVersion | test_snapshot_version_defaults_and_persisted | 不传 → 默认 v1.0；传 v1.1 → 写入 v1.1；老数据无 snapshot_version 字段 → 回退 v1.0 |
| TestCursorPagination | test_before_pagination_through_12_rows | 12 条、每页 3 条 → 4 页、12 条无重复、顺序严格 created_at DESC + id DESC |
| TestCursorPagination | test_same_created_at_tiebreak | 5 条同一 created_at、每页 2 条 → 3 页、5 条全到、顺序 id DESC |
| TestCursorPagination | test_before_invalid_cursor_400 | before= 非 base64 → 400、message 包含 cursor |
| TestCursorPagination | test_offset_mode_still_works_backwards_compat | 不传 before、offset=2 limit=2 total=6 → 仍正确；offset 模式"还有更多"时也产出 next_cursor |

> **⑥ 单测执行过程中顺手修了 2 个后端 bug：**
> 1. `preferences.py` cursor 模式原以"peek limit+1 那行未返回"当边界 → 下一页 before= 会严格跳过这 peek 行，造成尾部漏 1-2 条。修复：next_cursor 基于本页最后"已返回"行做 inclusive 边界。
> 2. `preferences.py` offset 模式原先 `next_cursor=None` → 前端首屏拉完 offset=0 后要继续翻页必须切回 offset 模式（不符合 Timeline 加载更多的 seamless）。修复：有更多时 offset 模式也同步产出 next_cursor（last returned row）。

---

## 四、已知非目标 / 未来增量
- 本仪表盘暂不做 **时间范围过滤（开始/结束时间）**、**CSV/JSON 导出**、**单用户 Drill-down** — 后续按 P7-04 再迭代。
- Cursor 分页目前只做 **向下翻（before= older）**，暂未做向上翻页的 `after=`；append-only Timeline 向下翻页满足场景。
- snapshot_version 当前恒 `"v1.0"`，后续结构大版本（AI 追问 schema 调整、新维度大字段新增）时 bump 到 `"v1.1"/"v2.0"`，供前端做分渲染 fallback。

---

## 五、验收要点（对应后续 ⑤ 手动验收）
1. **Settings → 账户 Tab：仪表盘可见**，默认 Stage=全部，能看到三张 Card（若没有任何 AI 调用，会全 0 / Outcome 暂无）。
2. **切换「最终」/「追问」**：segment 按钮 active 变色，刷新对应数据。
3. **点击「展开样本记录」**：最多 12 条，每行显示 Stage Pill + 画像命中 Pill + 快照数 + prompt 字 + sha1_10。
4. **Settings → 饮食偏好 Tab → 时间轴**：
   - 每页只显示 5 条，底部显示"加载更多历史快照（已加载 5/23）"
   - 点加载更多 → 再 5 条拼接（不要重复）→ 到末尾 → 显示"已到达末尾（共 N 条）"
   - 每条快照时间旁显示 `v1.0` chip（snapshot_version）
