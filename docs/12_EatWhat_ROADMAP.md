# EatWhat ROADMAP

> 2026-08-03 P0-07 勘误：任务阶段与进度真源是实施计划 P0–P8；GitHub Milestone 可用 v0.x 产品版本但以实施计划为准；与权威口径冲突处以《22_EatWhat_P0-07_技术文档收敛清单_v1.0》为准（5 候选、阈值配置化、共享无回链等）。  
>
> 文档状态：第四阶段正式交付物  
> 当前目标版本：v1.0.0  
> 文档日期：2026-07-21  
> 路线原则：先可运行，再完整；先 Mock，再 Live；先业务闭环，再视觉优化

---

# 1. 路线图目的

本文件用于持续记录：

- 当前版本；
- 里程碑；
- 功能边界；
- 后续版本；
- 已完成和未完成内容；
- GitHub Milestones；
- 版本发布条件；
- 暂缓功能；
- 需求变更。

ROADMAP与项目总进度文档的区别：

| 文档 | 作用 |
|---|---|
| 项目总体规划与进度跟踪 | 记录设计阶段和整体完成情况 |
| ROADMAP | 记录正式开发和产品版本演进 |

---

# 2. 版本总览

```text
v0.1  仓库与开发环境
v0.2  Mock问卷推荐闭环
v0.3  完整Mock页面
v0.4  数据库与推荐历史
v0.5  Supabase Auth与用户隔离
v0.6  每日额度与幂等
v0.7  地点与Mock POI
v0.8  高德Live
v0.9  AI Live与公共功能
v1.0  测试、部署和正式发布
v1.1  完整账户生命周期
v1.2  用户体验和推荐优化
```

版本号表示项目成熟度，不要求每个内部里程碑都公开发布GitHub Release。

---

# 3. 当前状态

```text
产品需求：完成
用户流程：完成
文字原型：完成
系统架构：完成
数据库：完成
API：完成
AI设计：完成
隐私安全：完成
开发计划：完成
正式代码：尚未开始
```

当前处于：

> **开发准备阶段**

---

# 4. v0.1：仓库与环境

## 目标

建立可重复启动、可持续提交、可自动检查的工程骨架。

## 范围

- [ ] 创建GitHub仓库
- [ ] Monorepo目录
- [ ] 导入docs
- [ ] React + TypeScript + Vite
- [ ] FastAPI
- [ ] `/health/live`
- [ ] 前端Lint
- [ ] 后端Lint
- [ ] 前端测试
- [ ] 后端测试
- [ ] GitHub Actions
- [ ] `.env.example`
- [ ] `.gitignore`
- [ ] README骨架
- [ ] LICENSE

## 发布条件

- 前端可启动；
- 后端可启动；
- CI全绿；
- 新开发者可以按README运行。

## GitHub Milestone

```text
Milestone: v0.1 Project Foundation
```

---

# 5. v0.2：第一条垂直切片

## 目标

完成第一个真正可操作的前后端业务闭环。

## 范围

- [ ] 固定问卷Schema
- [ ] 固定问卷页面
- [ ] 问卷进度
- [ ] Mock推荐Provider
- [ ] Mock推荐API
- [ ] 三种推荐结果
- [ ] 首选展示
- [ ] 更多推荐
- [ ] 加载和错误状态
- [ ] 前后端联调
- [ ] 单元测试
- [ ] 最小E2E测试

## 暂不包含

- 登录；
- 数据库；
- AI；
- 高德；
- 每日额度；
- 推荐历史。

## 发布条件

```text
答问卷
→ 后端返回3项
→ 前端展示
```

完整可运行。

## GitHub Milestone

```text
Milestone: v0.2 Vertical Slice
```

---

# 6. v0.3：完整Mock产品

## 目标

让全部主要页面可在没有真实服务时演示。

## 范围

- [ ] Mock登录
- [ ] Mock注册
- [ ] 首页
- [ ] 活动横幅
- [ ] 公共默认热门项
- [ ] 问卷摘要
- [ ] Mock补问
- [ ] 推荐结果
- [ ] 地点选择
- [ ] Mock商家
- [ ] 公共完整页
- [ ] Mock历史
- [ ] 设置
- [ ] 免责声明
- [ ] 隐私说明
- [ ] 404
- [ ] 响应式布局
- [ ] 手机底部导航
- [ ] 电脑顶部导航

## 发布条件

- 不需要任何外部Key即可完整演示；
- 手机和电脑均可使用；
- 无死链接；
- 所有关键异常状态可展示。

## GitHub Milestone

```text
Milestone: v0.3 Mock Product
```

---

# 7. v0.4：数据库与历史

## 目标

引入真实PostgreSQL业务数据。

## 范围

- [ ] SQLAlchemy Models
- [ ] Alembic
- [ ] app Schema
- [ ] profiles
- [ ] food_categories
- [ ] recommendation_sessions
- [ ] recommendation_items
- [ ] merchant_snapshots
- [ ] shared_choices
- [ ] feedback
- [ ] api_usage_logs
- [ ] Seed
- [ ] 推荐历史列表
- [ ] 推荐历史详情
- [ ] 删除和清空
- [ ] 数据库集成测试

## 发布条件

- 空数据库可通过迁移重建；
- Seed可重放；
- 推荐记录可保存和恢复；
- 历史不依赖浏览器LocalStorage。

## GitHub Milestone

```text
Milestone: v0.4 Database and History
```

---

# 8. v0.5：Supabase Auth

## 目标

建立正式云端用户体系。

## 范围

- [ ] 邮箱密码注册
- [ ] 注册成功自动登录
- [ ] 邮箱密码登录
- [ ] 会话恢复
- [ ] 退出登录
- [ ] 路由保护
- [ ] Supabase JWT验证
- [ ] profile触发器
- [ ] 用户资源隔离
- [ ] 一个公共演示账户
- [ ] Auth测试

## 暂不包含

- 邮箱验证；
- 忘记密码；
- 重置密码；
- 注销账户。

## 发布条件

- 用户A不能读取用户B数据；
- Token无效时拒绝；
- Secret不进入前端；
- 演示账号可登录。

## GitHub Milestone

```text
Milestone: v0.5 Authentication
```

---

# 9. v0.6：每日额度

## 目标

实现每天3次AI推荐的完整后端机制。

## 范围

- [ ] daily_ai_usage
- [ ] 北京时间业务日期
- [ ] used_count
- [ ] reserved_count
- [ ] 幂等键
- [ ] 推荐状态机
- [ ] 超时预留释放
- [ ] 首页额度
- [ ] 额度用完页面
- [ ] MockAI错误模拟
- [ ] 并发测试

## 发布条件

- 3次成功后第4次拒绝；
- AI失败不扣；
- 规则降级不扣；
- 并发无法绕过；
- 网络重试不重复扣。

## GitHub Milestone

```text
Milestone: v0.6 Quota and Idempotency
```

---

# 10. v0.7：地点和Mock POI

## 目标

完成完整地点与商家交互。

## 范围

- [ ] 浏览器定位
- [ ] 定位前说明
- [ ] 定位拒绝
- [ ] 手动地点搜索
- [ ] 演示地点
- [ ] 会话位置复用
- [ ] location_token
- [ ] Mock POI Provider
- [ ] 商家卡片
- [ ] 查看更多
- [ ] 无结果
- [ ] 地图失败
- [ ] 商家快照
- [ ] 位置隐私测试

## 发布条件

- 三种地点方式可用；
- 精确位置不入库；
- 拒绝定位不阻断；
- 商家结果可恢复。

## GitHub Milestone

```text
Milestone: v0.7 Location and Mock POI
```

---

# 11. v0.8：高德Live

## 目标

接入真实地点和附近商家数据。

## 范围

- [ ] 高德开发者应用
- [ ] Web服务Key
- [ ] 地点关键词搜索
- [ ] 反向地理编码
- [ ] 周边POI
- [ ] 分类关键词映射
- [ ] 响应标准化
- [ ] POI缓存
- [ ] Provider超时
- [ ] Provider错误码
- [ ] Live/Mock开关
- [ ] 全站用量记录

## 发布条件

- 前端无高德Secret；
- 真实地点查询成功；
- 真实附近商家可展示；
- Provider故障可回退；
- 无商家时有可用路径。

## GitHub Milestone

```text
Milestone: v0.8 Amap Live Integration
```

---

# 12. v0.9：AI Live与社区功能

## 目标

完成EatWhat最具展示价值的智能推荐和公共数据。

## AI范围

- [ ] food_categories完整词典
- [ ] 硬过滤
- [ ] 规则评分
- [ ] 动态候选
- [ ] AIProvider
- [ ] AI补问
- [ ] 最终三推荐
- [ ] JSON Schema
- [ ] 业务校验
- [ ] 一次重试
- [ ] 规则降级
- [ ] Token和成本日志
- [ ] Live/Mock开关

## 公共范围

- [ ] 主动匿名共享
- [ ] 公共聚合
- [ ] 至少3条阈值
- [ ] 同星期参考
- [ ] 默认热门项
- [ ] 首页3/5项
- [ ] 公共完整页
- [ ] 活动JSON
- [ ] 活动品牌搜索
- [ ] 满意度
- [ ] 设置与数据管理

## 发布条件

- AI只返回合法食物；
- AI不编造商家；
- 失败可降级；
- 成功才扣次数；
- 公共接口不返回单条用户数据；
- 默认热门项不显示虚构人数。

## GitHub Milestone

```text
Milestone: v0.9 AI and Community
```

---

# 13. v1.0.0：正式MVP

## 目标

将功能、测试、部署和文档整理为可公开展示的完整作品。

## 范围

### 测试

- [ ] 后端单元测试
- [ ] API集成测试
- [ ] 前端组件测试
- [ ] Playwright E2E
- [ ] 响应式测试
- [ ] 额度并发测试
- [ ] 越权测试
- [ ] XSS测试
- [ ] Secret扫描
- [ ] 依赖扫描

### 部署

- [ ] 前端托管
- [ ] FastAPI托管
- [ ] Supabase生产项目
- [ ] HTTPS
- [ ] CORS
- [ ] 健康检查
- [ ] Secret
- [ ] 数据库连接池
- [ ] 迁移流程
- [ ] 演示账户

### GitHub展示

- [ ] 完整README
- [ ] 架构图
- [ ] 产品截图
- [ ] 演示GIF
- [ ] 在线链接
- [ ] 本地运行说明
- [ ] Mock模式
- [ ] API文档
- [ ] 测试说明
- [ ] 隐私和免责声明
- [ ] 已知限制
- [ ] ROADMAP
- [ ] GitHub Release

## 发布条件

P0功能全部完成，发布门槛全部通过。

## GitHub Milestone

```text
Milestone: v1.0 MVP Release
```

---

# 14. v1.1：完整账户生命周期

## 目标

补齐第一版已设计但暂未开发的账户功能。

## 范围

- [ ] 强制或可选邮箱验证
- [ ] 验证邮件重发
- [ ] 忘记密码
- [ ] 重置密码
- [ ] 账户注销
- [ ] 个人数据删除
- [ ] 共享数据匿名化或删除
- [ ] 删除审计
- [ ] 联系开发者
- [ ] 数据导出（可选）
- [ ] 取消共享（可选）

## 技术前提

v1.0已经预留：

- user_id；
- 外键策略；
- shared_choices可置空；
- 认证路由；
- 设置页入口规划。

## GitHub Milestone

```text
Milestone: v1.1 Account Lifecycle
```

---

# 15. v1.2：体验与推荐优化

候选范围：

- [ ] 收藏食物类型
- [ ] 收藏商家
- [ ] 最近常吃
- [ ] 用户偏好开关
- [ ] 减少重复推荐
- [ ] 更丰富食物分类
- [ ] 规则权重调整
- [ ] Prompt A/B测试
- [ ] 推荐理由优化
- [ ] 社区时间段筛选
- [ ] 页面视觉升级
- [ ] 正式Logo
- [ ] 动效
- [ ] PWA基础能力
- [ ] 性能优化

不在拥有真实使用数据前开发复杂长期画像。

---

# 16. 暂不进入路线图的功能

以下功能当前不承诺版本：

- 手机号验证码；
- 微信登录；
- 支付；
- 会员；
- 商家入驻；
- 优惠券；
- 团购；
- 评论；
- 好友系统；
- 社交动态；
- 原生Android；
- 原生iOS；
- 营养和医疗建议；
- 实时排队；
- 外卖下单；
- 美团私有数据；
- 向量数据库；
- RAG；
- 微服务。

这些只有在v1.0完成后重新评估。

---

# 17. GitHub Issue分类

建议标签：

```text
type:feature
type:bug
type:test
type:docs
type:refactor
type:security
type:chore

area:frontend
area:backend
area:database
area:auth
area:ai
area:poi
area:community
area:deployment

priority:P0
priority:P1
priority:P2

status:blocked
status:ready
status:in-progress
status:review
```

---

# 18. Issue拆分原则

一个Issue应：

- 对应一个清晰成果；
- 可以独立验收；
- 修改范围有限；
- 有测试要求；
- 能在一个PR中完成。

好Issue：

> 实现GET /health/live并添加pytest测试。

差Issue：

> 完成全部后端。

---

# 19. Milestone与版本关系

| Milestone | 对应版本 |
|---|---|
| Project Foundation | v0.1 |
| Vertical Slice | v0.2 |
| Mock Product | v0.3 |
| Database and History | v0.4 |
| Authentication | v0.5 |
| Quota and Idempotency | v0.6 |
| Location and Mock POI | v0.7 |
| Amap Live Integration | v0.8 |
| AI and Community | v0.9 |
| MVP Release | v1.0 |
| Account Lifecycle | v1.1 |

---

# 20. 需求变更规则

PRD已经冻结。

新想法不得直接插入当前开发任务。

必须先判断：

## 20.1 属于缺陷

原有需求没有正确实现：

```text
进入当前版本修复
```

## 20.2 属于实现细节

不改变功能范围：

```text
可在当前里程碑调整
```

## 20.3 属于新功能

改变产品范围：

```text
记录Future Issue
→ 标记P2
→ 放到v1.1或以后
```

## 20.4 属于安全问题

优先级自动提升：

```text
立即评估
```

---

# 21. 每个版本的完成定义

版本只有满足以下条件才能标记完成：

- 对应Issue关闭；
- 自动测试通过；
- 手工验收通过；
- 文档更新；
- 无已知P0阻塞；
- 无Secret；
- GitHub Actions全绿；
- 提交Tag或更新里程碑状态。

---

# 22. 进度展示格式

README可显示：

```text
Current version: v0.2
Current milestone: Vertical Slice
Mock mode: Available
Live AI: Not connected
Live POI: Not connected
Production demo: Not deployed
```

不要在尚未实现时声称功能可用。

---

# 23. 推荐开发顺序

不可随意交换的关键依赖：

```text
仓库
→ Mock闭环
→ 页面
→ 数据库
→ Auth
→ 额度
→ 地点Mock
→ 高德
→ AI
→ 公共功能
→ 测试部署
```

可以并行的内容：

- 文档与基础页面；
- Mock数据与组件测试；
- 免责声明与设置页；
- README截图准备。

不应提前：

- 未有规则系统前直接接AI；
- 未有Provider抽象前写死高德；
- 未有数据库事务前上线额度；
- 未有权限测试前开放历史；
- 未有Mock模式前依赖所有外部服务。

---

# 24. 公开发布策略

## 24.1 v0.x

可以持续推送到GitHub，但不一定部署正式Live服务。

## 24.2 首个在线Demo

建议在：

```text
v0.3 Mock Product
```

之后部署Mock演示。

这样即使真实服务未接入，也有可访问成果。

## 24.3 Live Demo

建议在：

```text
v0.9
```

之后开放，并配置：

- 演示账户；
- 全站AI限额；
- POI缓存；
- 服务开关；
- 隐私说明。

## 24.4 v1.0 Release

作为简历和作品集版本。

---

# 25. 成功指标

本项目不以真实商业增长为主要目标。

## 25.1 工程成功

- 可复现；
- 可测试；
- 可部署；
- 可维护；
- 无Secret；
- 有完整文档；
- 有清晰Commit；
- 有真实前后端闭环。

## 25.2 产品成功

- 用户能在较少步骤内得到食物类型；
- 用户明确选择时不浪费AI；
- AI故障时仍能完成；
- 公共冷启动不空白；
- 手机和电脑均可用。

## 25.3 作品集成功

README能清楚展示：

- 问题；
- 产品方案；
- 技术架构；
- 难点；
- 测试；
- 安全；
- 在线Demo；
- 未来规划。

---

# 26. 路线图更新规则

每次里程碑完成后：

1. 勾选对应内容；
2. 更新Current version；
3. 关闭GitHub Milestone；
4. 创建下一个Milestone；
5. 更新README；
6. 更新项目总进度；
7. 发布阶段截图；
8. 记录重要架构变更。

---

# 27. 下一步

当前路线图已经完成。

下一份优先文档：

```text
11_EatWhat_测试与验收计划.md
13_EatWhat_GitHub仓库与协作规范.md
```

完成后，四个设计阶段全部结束，可以进入：

```text
正式创建GitHub仓库与编写代码
```
