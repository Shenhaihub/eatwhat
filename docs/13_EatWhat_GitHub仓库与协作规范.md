# EatWhat GitHub 仓库与协作规范

> 2026-08-03 P0-07 勘误：文档引用名与进度真源以《22_EatWhat_P0-07_技术文档收敛清单_v1.0》及实施计划为准；共享无回链等约束纳入仓库验收清单。  
>
> 文档状态：第四阶段正式交付物  
> 产品版本：v1.0 MVP  
> 文档日期：2026-07-22  
> 适用人员：项目作者、Codex及未来协作者  
> 协作模式：单仓库、短分支、Pull Request、自动检查、小步合并

---

# 1. 文档目的

本文档规定 EatWhat 在 GitHub 上的：

- 仓库结构；
- 分支策略；
- Commit格式；
- Issue和Milestone；
- Pull Request流程；
- Codex使用边界；
- 代码审查；
- GitHub Actions；
- Secret保护；
- 依赖更新；
- Release；
- 文档维护；
- 紧急修复；
- M0仓库初始化清单。

目标不是模仿大型企业流程，而是让第一个完整项目具备：

- 可追踪；
- 可回滚；
- 可审查；
- 可测试；
- 可展示；
- 不容易被AI一次性改乱。

---

# 2. 仓库基本信息

## 2.1 仓库名称

建议：

```text
eatwhat
```

GitHub完整名称：

```text
<your-github-username>/eatwhat
```

## 2.2 仓库可见性

开发初期可选择：

```text
Private
```

完成Mock闭环或准备作品展示后切换：

```text
Public
```

切换公开前必须完成Secret检查。

## 2.3 默认分支

```text
main
```

不使用`master`和`develop`双长期分支。

## 2.4 开源协议

个人作品建议初步采用：

```text
MIT License
```

但以下内容不因此自动获得再授权：

- 品牌活动图片；
- 商家Logo；
- 第三方地图数据；
- 第三方字体；
- 受版权保护素材。

仓库中只放有明确授权或自制的资源。

---

# 3. Monorepo结构

```text
eatwhat/
├── .github/
│   ├── ISSUE_TEMPLATE/
│   │   ├── bug_report.yml
│   │   ├── feature_request.yml
│   │   ├── task.yml
│   │   └── config.yml
│   ├── workflows/
│   │   ├── ci-frontend.yml
│   │   ├── ci-backend.yml
│   │   ├── ci-e2e.yml
│   │   ├── dependency-review.yml
│   │   └── release.yml
│   ├── CODEOWNERS
│   ├── pull_request_template.md
│   └── dependabot.yml
├── frontend/
├── backend/
├── supabase/
├── docs/
├── scripts/
├── .editorconfig
├── .gitignore
├── .env.example
├── LICENSE
├── README.md
├── SECURITY.md
├── CONTRIBUTING.md
└── docker-compose.yml
```

## 3.1 单一事实来源

| 内容 | 主要位置 |
|---|---|
| 产品范围 | `docs/01_PRD.md` |
| 页面流程 | `docs/02_用户流程.md` |
| API合同 | `docs/07_API设计.md` |
| 数据库结构 | Alembic Migration + `docs/06_数据库设计.md` |
| 版本计划 | `docs/12_ROADMAP.md` |
| 开发状态 | GitHub Milestones + 总进度文档 |
| 环境变量 | `.env.example` |
| 运行说明 | `README.md` |

不得只在聊天记录中做重要决定而不同步文档。

---

# 4. 分支策略

## 4.1 采用短生命周期分支

每个Issue创建一个分支，完成后通过PR合并到`main`。

```text
main
  ├── feat/12-questionnaire-page
  ├── fix/31-quota-double-charge
  ├── test/42-auth-isolation
  └── docs/18-update-readme
```

## 4.2 分支命名

格式：

```text
<type>/<issue-number>-<short-description>
```

类型：

```text
feat
fix
test
docs
refactor
chore
security
hotfix
```

示例：

```text
feat/12-questionnaire-page
feat/25-mock-recommendation-api
fix/31-quota-idempotency
security/54-redact-location-logs
docs/8-import-design-docs
```

## 4.3 分支寿命

建议：

- 一个清晰任务；
- 一个PR；
- 尽快合并；
- 不长期与main分离。

一个分支不应跨越多个Milestone。

## 4.4 不直接推送main

正式启用规则后：

```text
不在main直接开发
不force push main
不删除main
```

即使只有一个开发者，也通过PR合并，保留检查和变更记录。

---

# 5. main分支保护

建议创建GitHub Ruleset或Branch Protection。

## 5.1 必须规则

- Require a pull request before merging；
- Require status checks to pass；
- Require branches to be up to date before merging；
- Block force pushes；
- Block deletion；
- Require conversation resolution；
- Restrict bypass权限；
- 对管理员也尽量生效。

## 5.2 单人项目的Review设置

如果只有用户本人：

- 保持必须通过PR；
- 不强制“1名其他人员批准”，否则自己无法合并；
- 依靠CI、PR检查表和人工自审；
- 有协作者后再要求至少1个Approval。

## 5.3 必需状态检查

初期：

```text
frontend-lint-test-build
backend-lint-type-test
```

后续加入：

```text
database-integration
e2e-smoke
dependency-review
```

检查名称要稳定。分支规则引用的检查名称不能频繁改名。

---

# 6. Issue体系

## 6.1 所有代码任务先有Issue

即使是Codex执行，也先创建Issue。

Issue负责回答：

- 为什么做；
- 做什么；
- 不做什么；
- 修改范围；
- 验收标准；
- 测试要求；
- 所属Milestone。

## 6.2 Issue类型

### Feature

新功能。

### Bug

已有需求没有正确实现。

### Task

工程、配置、迁移、文档和维护任务。

### Security

安全与隐私问题。

安全漏洞若包含敏感细节，不创建公开Issue，使用私有安全报告或私有仓库处理。

## 6.3 Issue标题

格式：

```text
[Area] 动词 + 明确成果
```

示例：

```text
[Backend] Implement GET /health/live
[Frontend] Build questionnaire step component
[Auth] Verify Supabase JWT in FastAPI
[Quota] Prevent duplicate AI quota consumption
[Docs] Add local setup instructions
```

## 6.4 Feature Issue模板内容

```text
背景：

目标：

范围：
- 

不包含：
- 

关联文档：
- docs/...

接口或数据结构：

验收标准：
- [ ]

测试要求：
- [ ]

风险：

Milestone：
```

## 6.5 Bug模板内容

```text
环境：

前置条件：

复现步骤：
1.
2.
3.

实际结果：

预期结果：

request_id：

严重级别：

隐私检查：
附件是否包含Token、邮箱、位置、Secret？
```

---

# 7. 标签规范

## 7.1 类型

```text
type:feature
type:bug
type:test
type:docs
type:refactor
type:security
type:chore
```

## 7.2 区域

```text
area:frontend
area:backend
area:database
area:auth
area:quota
area:ai
area:poi
area:community
area:deployment
area:docs
```

## 7.3 优先级

```text
priority:P0
priority:P1
priority:P2
priority:P3
```

## 7.4 状态

```text
status:ready
status:blocked
status:in-progress
status:review
status:needs-info
```

## 7.5 AI协作

可增加：

```text
agent:codex
agent:manual
```

用于记录主要实现方式，但不能把“AI生成”当作无需审查的理由。

---

# 8. Milestone规范

Milestone对应ROADMAP：

```text
v0.1 Project Foundation
v0.2 Vertical Slice
v0.3 Mock Product
v0.4 Database and History
v0.5 Authentication
v0.6 Quota and Idempotency
v0.7 Location and Mock POI
v0.8 Amap Live Integration
v0.9 AI and Community
v1.0 MVP Release
v1.1 Account Lifecycle
```

## 8.1 一个Issue只归属一个当前Milestone

跨版本工作要拆分。

例如：

```text
Auth基础登录 → v0.5
邮箱验证 → v1.1
```

不能把两个版本需求混在同一个Issue。

---

# 9. Commit规范

采用Conventional Commits风格：

```text
<type>(<scope>): <summary>
```

## 9.1 类型

```text
feat
fix
test
docs
refactor
perf
security
chore
ci
build
revert
```

## 9.2 Scope

```text
frontend
backend
auth
quota
ai
poi
db
community
docs
ci
```

## 9.3 示例

```text
feat(frontend): add questionnaire step navigation
feat(backend): add mock recommendation endpoint
fix(quota): release reservation after provider timeout
test(auth): add cross-user history access tests
docs(api): document recommendation idempotency
ci: add frontend validation workflow
```

## 9.4 摘要要求

- 使用英文或统一中文，项目建议英文；
- 使用动词；
- 简短；
- 不加句号；
- 说明结果，不写“update files”。

## 9.5 Commit粒度

好：

```text
实现一个接口和对应测试
```

差：

```text
一次提交前端、后端、文档、依赖和无关格式化
```

## 9.6 不允许的Commit

```text
fix
update
修改一下
final
final2
try again
codex changes
```

---

# 10. Pull Request流程

## 10.1 标准流程

```text
Issue
→ 创建分支
→ 实现
→ 本地测试
→ 自查diff
→ Push
→ Draft PR
→ CI
→ 修复
→ Ready for review
→ 合并
→ 删除分支
```

## 10.2 Draft PR

开始编码后可以尽早创建Draft PR，便于：

- 关联Issue；
- 查看CI；
- 记录进度；
- 防止任务长期只在本地。

## 10.3 PR标题

与Commit风格一致：

```text
feat(frontend): add questionnaire flow
```

## 10.4 PR正文模板

```text
## Summary
- 

## Related issue
Closes #

## Scope
- 

## Out of scope
- 

## Testing
- [ ] Unit tests
- [ ] Integration tests
- [ ] Manual verification

Commands run:
```text
...
```

## Screenshots

## Security and privacy
- [ ] No secrets added
- [ ] No token or precise location logged
- [ ] Authorization checked
- [ ] User input validated

## Documentation
- [ ] Updated if required

## Reviewer notes
```

## 10.5 PR大小

推荐：

- 只解决一个Issue；
- 尽量少于约400行有意义变更；
- 自动生成文件和Lock文件单独说明；
- 超大功能拆分为多个可合并步骤。

400行不是硬限制。重点是可以人工理解。

---

# 11. 自我代码审查清单

提交PR前逐项检查。

## 11.1 功能

- [ ] 符合Issue验收标准
- [ ] 没有擅自增加新功能
- [ ] 正常、空、错误状态已处理
- [ ] 页面流程与文档一致

## 11.2 后端

- [ ] API层没有复杂业务逻辑
- [ ] Service和Repository职责清楚
- [ ] 输入使用Pydantic校验
- [ ] 数据库操作有事务边界
- [ ] 当前用户权限已检查
- [ ] 错误响应不暴露内部信息

## 11.3 前端

- [ ] TypeScript无any滥用
- [ ] 加载和错误状态存在
- [ ] 手机端可用
- [ ] 表单有label
- [ ] 用户文本不会作为HTML执行
- [ ] 不在组件中硬编码Secret

## 11.4 安全隐私

- [ ] 无Secret
- [ ] 无Token日志
- [ ] 无精确坐标持久化
- [ ] AI输入无身份位置
- [ ] 删除和共享规则正确
- [ ] 额度不可被前端绕过

## 11.5 测试

- [ ] 新增或更新测试
- [ ] 本地命令通过
- [ ] CI通过
- [ ] Mock错误场景已验证

## 11.6 文档

- [ ] API变化同步文档
- [ ] 环境变量同步`.env.example`
- [ ] 用户行为变化同步流程/README
- [ ] ROADMAP状态更新

---

# 12. 合并策略

## 12.1 默认使用Squash and Merge

优点：

- main历史简洁；
- 一个PR对应一个Commit；
- 易回滚；
- AI产生的中间修复Commit不会污染main。

Squash Commit标题使用PR标题。

## 12.2 禁用或少用

- Merge Commit：项目初期不需要复杂合并历史；
- Rebase and Merge：可用，但不作为默认；
- 直接push main：禁用。

## 12.3 合并后

- 删除远程分支；
- 本地更新main；
- 关闭Issue；
- 更新Milestone进度；
- 必要时更新总进度文档。

---

# 13. Codex协作规范

## 13.1 Codex的角色

Codex可以：

- 阅读指定文档；
- 实现明确Issue；
- 创建或修改指定文件；
- 运行测试；
- 修复测试；
- 生成PR草稿；
- 解释代码。

Codex不能被默认授权：

- 修改整个架构；
- 改变冻结需求；
- 删除大量文件；
- 直接使用生产Secret；
- 直接部署生产；
- 直接合并main；
- 自行决定新增依赖；
- 跳过测试。

## 13.2 每次任务输入

```text
Issue：#编号

目标：

背景文档：
- docs/...

允许修改：
- 文件或目录

禁止修改：
- 文件或目录

接口合同：

验收标准：
- [ ]

测试命令：

安全要求：

输出要求：
说明修改、测试结果和剩余风险。
```

## 13.3 Codex开始前

要求Codex先：

1. 阅读Issue；
2. 阅读指定文档；
3. 查看相关代码；
4. 给出简短实施计划；
5. 不改代码之外的无关内容。

## 13.4 Codex完成后

必须返回：

- 修改文件清单；
- 关键实现说明；
- 执行的测试；
- 测试结果；
- 未完成内容；
- 风险；
- 是否修改依赖和环境变量。

## 13.5 人工检查

即使测试全绿，也要检查：

- Diff；
- API权限；
- Secret；
- 日志；
- 删除逻辑；
- 额度逻辑；
- 第三方调用；
- 是否偏离需求。

## 13.6 AI生成代码标注

Commit不需要写“AI generated”。

责任仍属于提交者。

可以在PR说明：

```text
Implementation assistance: Codex
Human review: completed
```

---

# 14. CODEOWNERS

个人仓库可创建：

```text
* @your-github-username
/frontend/ @your-github-username
/backend/ @your-github-username
/docs/ @your-github-username
/.github/ @your-github-username
```

作用：

- 明确责任；
- 未来加入协作者时可自动请求审查；
- 保护工作流和安全文件。

只有个人时，不强制Code Owner Approval。

未来团队可改为：

```text
/frontend/ @org/frontend-team
/backend/ @org/backend-team
/.github/ @org/security-team
```

---

# 15. GitHub Actions规范

## 15.1 工作流权限

工作流顶部显式设置最小权限：

```yaml
permissions:
  contents: read
```

只有确需写权限的工作流单独增加。

## 15.2 PR工作流

触发：

```yaml
on:
  pull_request:
```

运行：

- 前端Lint、Type、Test、Build；
- 后端Ruff、mypy、pytest；
- 依赖审查；
- 后续数据库集成和E2E。

## 15.3 main工作流

触发：

```yaml
on:
  push:
    branches: [main]
```

运行：

- 全部PR检查；
- Staging构建或部署；
- Smoke Test。

## 15.4 发布工作流

触发：

```yaml
on:
  push:
    tags:
      - 'v*'
```

运行：

- 完整测试；
- 构建；
- Migration检查；
- 部署；
- Release说明。

## 15.5 Action版本安全

第三方Action应：

- 优先使用可信官方或知名维护者；
- 在安全敏感或部署工作流中固定到完整Commit SHA；
- Dependabot更新Action引用；
- 审查Action权限和源代码；
- 避免来源不明Action。

可在注释中标明对应版本：

```yaml
uses: actions/checkout@<full-commit-sha> # v4.x
```

## 15.6 Fork PR安全

不要在不可信PR的普通`pull_request`工作流中暴露Secret。

谨慎使用：

```text
pull_request_target
```

本项目初期避免使用它运行PR中的代码。

## 15.7 缓存

可缓存：

- npm缓存；
- uv/Python依赖缓存；
- Playwright浏览器。

缓存Key必须包含Lock文件哈希。

不要缓存：

- `.env`；
- Token；
- 构建Secret；
- 用户数据。

---

# 16. CI工作流建议

## 16.1 `ci-frontend.yml`

步骤：

```text
checkout
setup-node
npm ci
npm run lint
npm run typecheck
npm run test
npm run build
```

## 16.2 `ci-backend.yml`

步骤：

```text
checkout
install uv
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest
```

## 16.3 `ci-e2e.yml`

初期可手工或main运行：

```text
启动Mock后端
启动前端
安装Playwright
运行Chromium Smoke
失败上传Trace
```

发布前运行多浏览器Projects。

## 16.4 数据库集成

后续使用GitHub Actions Service Container或本地Supabase环境。

必须：

- 使用测试数据库；
- 从Migration创建；
- 不连接生产库。

---

# 17. Secret管理

## 17.1 本地

```text
.env
frontend/.env.local
backend/.env
```

必须在`.gitignore`。

## 17.2 GitHub Actions

放入：

```text
Repository Secrets
Environment Secrets
```

例如：

```text
STAGING_DATABASE_URL
STAGING_SUPABASE_SECRET
STAGING_AI_API_KEY
STAGING_AMAP_API_KEY
```

## 17.3 环境隔离

建立：

```text
staging
production
```

生产Secret只给生产环境工作流。

可对production设置人工审批。

## 17.4 禁止

- 在Issue贴Secret；
- 在PR截图显示Secret；
- 在测试日志打印Secret；
- 把Secret放在`VITE_`变量；
- 把生产`.env`交给Codex；
- 在命令历史中公开完整Token。

## 17.5 泄露响应

```text
立即撤销
→ 生成新Key
→ 更新Secret
→ 检查调用日志
→ 清理Git历史
→ 创建安全复盘
```

仅删除当前文件中的Secret不够，Git历史中仍可能存在。

---

# 18. Dependabot与依赖管理

## 18.1 配置范围

```text
npm
pip/uv相关依赖
GitHub Actions
```

## 18.2 更新频率

建议每周。

## 18.3 更新策略

- Patch：测试通过后可快速合并；
- Minor：检查变更说明；
- Major：单独Issue和测试；
- 安全更新：提高优先级；
- Lock文件必须一并提交。

## 18.4 不自动无审查合并

依赖PR仍需：

- CI；
- 变更审查；
- 运行关键E2E；
- 检查供应链风险。

## 18.5 依赖审查

PR修改依赖清单时运行Dependency Review，识别新增漏洞依赖。

---

# 19. 文档规范

## 19.1 代码变化需要同步文档

| 变化 | 更新 |
|---|---|
| 新环境变量 | `.env.example` + README |
| API变化 | API设计/OpenAPI |
| 数据库变化 | Migration + 数据库文档 |
| 页面行为变化 | 用户流程/原型 |
| 版本范围变化 | PRD/ROADMAP |
| 安全规则变化 | SECURITY/隐私文档 |

## 19.2 Markdown检查

建议后续加入Markdown Lint，但不阻碍M0启动。

## 19.3 文档编号

保持现有编号，不随意重命名造成引用失效。

---

# 20. README结构

```text
项目标题与一句话介绍
Demo与截图
核心功能
产品流程
技术栈
系统架构图
项目结构
本地运行
Mock模式
Live模式
环境变量
测试
API文档
数据库迁移
隐私与安全
已知限制
ROADMAP
License
```

## 20.1 README必须诚实

尚未实现的功能标记：

```text
Planned
In progress
Mock only
Live available
```

不能把规划写成已完成功能。

---

# 21. Release规范

## 21.1 版本号

使用语义化版本：

```text
v0.1.0
v0.2.0
...
v1.0.0
v1.0.1
v1.1.0
```

## 21.2 Release内容

```text
Highlights
Added
Changed
Fixed
Security
Known limitations
Migration notes
Demo link
```

## 21.3 Tag

Tag必须指向main中通过CI的Commit。

不要在本地未推送或未测试分支创建正式Release。

## 21.4 Pre-release

v0.x或候选版本可标记：

```text
Pre-release
```

v1.0.0满足发布验收表后再标记正式。

---

# 22. Hotfix流程

生产P0问题：

```text
main
→ hotfix/<issue>-description
→ 最小修复
→ 测试
→ PR
→ 合并
→ Patch Release
```

例如：

```text
hotfix/201-user-history-authorization
```

修复后：

- 新增回归测试；
- 发布`v1.0.1`；
- 更新安全或事故记录；
- 不只在生产手工改代码。

---

# 23. 回滚规范

## 23.1 代码回滚

优先：

- Revert PR；
- 回滚部署版本。

不改写main历史。

## 23.2 数据库回滚

每个Migration必须评估：

- 向前修复；
- 是否可安全Downgrade；
- 数据是否会丢失；
- 部署顺序。

生产数据Migration不能靠简单`git revert`解决。

## 23.3 记录

回滚Issue包含：

- 触发原因；
- 影响；
- Commit；
- Migration；
- 处理；
- 后续修复。

---

# 24. 安全报告

创建`SECURITY.md`：

- 支持版本；
- 报告方式；
- 不要公开披露漏洞细节；
- 预期响应；
- 不提供漏洞赏金承诺。

公开仓库可启用GitHub Private Vulnerability Reporting（若账户和仓库功能可用）。

---

# 25. 贡献规范

创建`CONTRIBUTING.md`：

- 开发环境；
- Issue优先；
- 分支命名；
- Commit；
- PR；
- 测试；
- 文档；
- 安全；
- 行为规范。

个人项目初期可以简化，但仓库公开后有助于展示工程能力。

---

# 26. 自动生成文件

以下文件不能手工随意编辑：

- `package-lock.json`；
- `uv.lock`；
- 自动生成OpenAPI文件；
- Migration自动生成部分。

修改依赖时使用正确包管理命令。

PR中解释大规模Lock文件变化的原因。

---

# 27. 格式化变更

不要把全项目格式化与功能变更混在同一个PR。

如需首次格式化：

```text
单独chore PR
```

优点：

- 以后Diff清楚；
- `git blame`更有意义；
- 功能审查不被噪声淹没。

---

# 28. 初始GitHub Project建议

可选使用GitHub Project表格：

列：

```text
Backlog
Ready
In Progress
In Review
Done
```

字段：

```text
Status
Priority
Milestone
Area
Estimate（可选）
```

初学项目不必使用复杂Story Point。

---

# 29. M0初始Issue清单

建议仓库创建后立即建立：

## Issue 1

```text
[Repo] Initialize monorepo structure
```

## Issue 2

```text
[Frontend] Initialize React TypeScript Vite app
```

## Issue 3

```text
[Backend] Initialize FastAPI app and health endpoint
```

## Issue 4

```text
[CI] Add frontend validation workflow
```

## Issue 5

```text
[CI] Add backend validation workflow
```

## Issue 6

```text
[Docs] Import approved EatWhat design documents
```

## Issue 7

```text
[Repo] Add README, license, contribution and security files
```

每个Issue分别创建PR，不建议一个PR一次完成全部M0任务。

---

# 30. 仓库创建前文件清单

必须准备：

- [x] PRD
- [x] 用户流程
- [x] 信息架构
- [x] 文字原型
- [x] 系统架构
- [x] 数据库设计
- [x] API设计
- [x] AI设计
- [x] 隐私安全
- [x] MVP开发计划
- [x] 测试与验收计划
- [x] ROADMAP
- [x] GitHub协作规范

创建仓库时再生成：

- [ ] README
- [ ] LICENSE
- [ ] CONTRIBUTING
- [ ] SECURITY
- [ ] Issue模板
- [ ] PR模板
- [ ] CODEOWNERS
- [ ] Dependabot
- [ ] Workflows
- [ ] `.gitignore`
- [ ] `.editorconfig`
- [ ] `.env.example`

---

# 31. 仓库验收标准

## 31.1 结构

- Monorepo目录正确；
- 文档已导入；
- Lock文件存在；
- 无临时文件。

## 31.2 Git流程

- main受保护；
- PR检查生效；
- 不能force push；
- Squash merge可用；
- 分支合并后删除。

## 31.3 CI

- 前端检查通过；
- 后端检查通过；
- 失败能阻止合并；
- 工作流权限最小；
- 不暴露Secret。

## 31.4 协作

- Issue模板可用；
- PR模板可用；
- 标签建立；
- Milestone建立；
- Codex任务可通过Issue执行。

## 31.5 展示

- README说明当前进度；
- 未实现功能没有伪装完成；
- ROADMAP可访问；
- License清楚；
- 安全报告方式存在。

---

# 32. 四阶段完成后的第一批行动

```text
1. 创建GitHub仓库eatwhat
2. 将main设为默认分支
3. 导入docs
4. 创建v0.1 Milestone
5. 创建M0的7个Issue
6. 创建第一个chore分支
7. 初始化根目录
8. 提交第一个PR
9. 配置Ruleset
10. 开始React和FastAPI初始化
```

---

# 33. 最终原则

```text
Issue定义目标
文档约束需求
分支隔离改动
PR承载审查
CI验证质量
人类对AI代码负责
main始终可运行
Release只来自通过测试的main
```

本规范完成后，EatWhat的四阶段设计工作全部结束，可以正式进入M0开发。
