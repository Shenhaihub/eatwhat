# EatWhat Frontend（React 19 · TypeScript 6 · Vite 8）

> 面向移动端优先的响应式 Web：首页 Hero + 问卷推荐 + 社区 + 附近商家 + 设置。
> 与后端通过 `services/api/client.ts` 严格契约对接（后端 Pydantic v2 模型 → 本端 `services/api/types/*.ts` 镜像）。

## 0. 交付概览（对应 A / B 冲刺）

| 页面 / 模块 | 位置 | 说明 |
|---|---|---|
| 🏠 Home | `pages/Home.tsx` | Hero + 「开始推荐 / 看看大家在吃什么」双 CTA；**顶部插入 `<CampaignBanner />` 活动横幅** |
| 🎉 CampaignBanner | `components/CampaignBanner.tsx` | 内置 2 条活动（打卡送 AI 额度 / 本周主题 PK）；每条独立 localStorage 记「首次关闭时间」，**7 天内不再自动弹** |
| 🧭 Recommend | `pages/Recommend.tsx` | 问卷 → 结果页（1→3→5 渐进展开 / 来源 chip / 查附近）；**AI 开关默认关**，勾选需登录并传 `prefer_ai_gain=true` |
| 🎪 Community | `pages/Community.tsx` | 主题横幅(`#theme`) + Feed 🔥/⏰ tab + 点赞乐观更新 + 今日 Top5 榜 + 右下「分享今天吃了啥」FAB |
| 🏪 Nearby | `pages/Nearby.tsx` | `?food_code=xxx` 直达；高德 / Mock POI；主商户 + 折叠其他 + 来源 chip |
| ⚙️ Settings | `pages/Settings.tsx` | `?tab=preference` 支持外部按钮锚点直达（修复了 Recommend 结果页两个「查看偏好时间轴」按钮） |
| 📜 History / About / Privacy / Disclaimer / Login / AuthCallback / NotFound | `pages/*.tsx` | 配套页面；History / Settings 走 `<ProtectedRoute>` |
| 🧩 AppShell | `components/layout/AppShell.tsx` | `<Header />`（桌面导航「大家在吃」）+ `<main />` + `<MobileNav />`（移动端 4 Tab：首页/社区/推荐/我的） |
| 🔐 AuthContext | `context/AuthContext.tsx` | Supabase Magic Link；JWT 通过 API 客户端自动注入 `Authorization: Bearer <token>`；注入钩子兼容 E2E session |
| 🧪 单测 | 散布 `*.test.ts(x)` | vitest（27 条）+ @testing-library/react + jsdom |

---

## 1. 环境 & 命令

### 1.1 前置

- **Node.js**：`>= 24 LTS`（用你习惯的 nvm / volta / fnm）
- **npm**：随 Node 24 自带
- 后端需要并行启动在 `http://127.0.0.1:8000`（Vite 已配 server.proxy → `/api/*` 转发过去，**不用手动配跨域**）

### 1.2 命令速记

```powershell
cd frontend

# 首次 / 拉新依赖后
npm install        # 或者 npm ci（严格按 package-lock.json 安装，CI 用这个）

# 开发（热更新）
npm run dev        # http://localhost:5173/  ·  Vite proxy 已把 /api → http://127.0.0.1:8000

# 质量门禁（4 条都要过）
npm run typecheck  # tsc -b  ·  禁止 any 逃逸；CI 把这个当 fail-fast
npm run lint       # oxlint（103 条规则，不含 type-aware 那套）
npm run test       # vitest run（jsdom + testing-library）
npm run build      # tsc + vite build → dist/（部署产物）

# 生产预览（在 build 之后，确认打包产物 OK）
npm run preview
```

### 1.3 环境变量

根目录 `.env.local`（推荐）或 `.env`：

```dotenv
# 必配（如果要用登录 + Supabase 认证）：
VITE_SUPABASE_URL=https://your-project-ref.supabase.co
VITE_SUPABASE_ANON_KEY=eyJhbGciOi...

# 可选（一般不需要，Vite 默认按相对路径部署都能工作）：
# VITE_API_BASE_URL=/api/v1       # 只在你要把前端部署到"非同源后端"时才改
```

> **AI / POI / 社区数据不需要前端 Key，全部走后端 `/api/*` 统一代理。** 前端本地开发不填任何变量也能跑（MVP Mock）。

---

## 2. 代码结构

```text
frontend/
├── index.html                  # Vite 入口
├── vite.config.ts              # server.proxy: /api -> http://127.0.0.1:8000  ·  test: { environment: 'jsdom' }
├── tsconfig*.json              # TS 6（strict；noUncheckedIndexedAccess）+ TSProject References
├── .oxlintrc.json / .prettierrc.json
├── public/                     # favicon.svg 等静态资源（构建后原样复制到 dist）
└── src/
    ├── main.tsx                # 入口：<ErrorBoundary><AuthProvider><BrowserRouter><App />
    ├── App.tsx                 # 路由表：AppShell nested routes + /login + /auth/callback + 404
    │
    ├── pages/                  # 页面级组件（路由叶子节点）
    │   ├── Home.tsx                # 入口 Hero + CampaignBanner
    │   ├── Recommend.tsx           # 问卷+结果（Recommend.RecGen.test.tsx 单独测 AI 增益切换）
    │   ├── Community.tsx           # B：主题横幅 + Feed tab + Top 榜 + FAB
    │   ├── Nearby.tsx              # ?food_code=xxx 查附近（12 条 vitest）
    │   ├── Settings.tsx            # ?tab=preference 解析 useSearchParams
    │   ├── History.tsx             # 来源 chip 映射
    │   ├── Login.tsx / AuthCallback.tsx
    │   ├── About.tsx / Privacy.tsx / Disclaimer.tsx / NotFound.tsx / Activity.tsx
    │   └── *.test.ts(x)            # vitest（和源码放一起，就近读）
    │
    ├── components/
    │   ├── CampaignBanner.tsx      # B：活动横幅（2 条内置 + 7 天关闭记忆）
    │   ├── layout/
    │   │   ├── AppShell.tsx        # Header + MobileNav + main
    │   │   ├── Header.tsx          # 桌面导航：首页 / 大家在吃 / 开始推荐；右上角登录入口
    │   │   └── MobileNav.tsx       # 移动端 4 Tab：首页/社区/推荐/我的
    │   ├── profile/
    │   │   ├── PreferenceProfile.tsx   # 设置页画像 Tab
    │   │   └── HistoryInline.tsx        # 设置页推荐历史
    │   └── common/
    │       └── ErrorBoundary.tsx   # 顶层兜底（展示友好错误页 + 重试）
    │
    ├── context/
    │   └── AuthContext.tsx         # Supabase 会话；useAuth() 暴露 isAuthenticated/user/accessToken/sendMagicLink/logout/refresh
    │
    ├── services/api/               # 与后端严格 1:1 契约
    │   ├── client.ts               # api.{get/post/patch/delete} + 业务门面（communityFeed/communityThemeVote/…）
    │   ├── client.test.ts          # 3 条：错误体解析 / request_id / 401 自动清
    │   └── types/
    │       ├── index.ts            # 再导出，业务代码只需 `import type {...} from './types'`
    │       ├── community.ts        # B：CommunityFeed* / Theme* / Trending* / LikeResponse
    │       ├── recommendations.ts  # A：generation_mode 相关 / final_reason 类型
    │       ├── questionnaire.ts
    │       ├── poi.ts              # Nearby 契约
    │       ├── history.ts / preferences.ts / location.ts / food.ts / system.ts / enums.ts
    │
    ├── lib/
    │   ├── sourceBadge.ts          # 9 种 final_reason → 颜色 + 文案（Recommend/Settings/History 共用）
    │   └── supabase.ts             # getSupabase()（VITE_SUPABASE_*；CI 缺配置时返回 null 不抛）
    │
    ├── styles/
    │   ├── tokens.css              # 设计 Token：--color-* / --space-* / --radius-*
    │   ├── global.css              # 基础排版 / 按钮 / chip / 页面壳
    │   ├── recommendations.css     # 结果页渐进展开 / 来源 chip
    │   └── nearby.css              # 附近商家卡片
    │
    └── test/
        └── setup.ts                # vitest setup：@testing-library/jest-dom + 全局 mocks
```

---

## 3. 前后端对接约定（**新接口必读**）

### 3.1 Base URL & 前缀
- Dev：Vite `server.proxy` 把 `/api/*` → `http://127.0.0.1:8000`；前端请求一律写 `/api/v1/...`，**不写域名**。
- Prod：Nginx 镜像已配 `location /api/ { proxy_pass http://backend:8000/; }`（见 `frontend/Dockerfile` 末尾 + C2）。

### 3.2 统一响应体 & 错误体
```ts
// 成功：直接返回 payload（T）
// 失败：固定结构，任何非 2xx 都走它
export interface ApiErrorPayload {
  error: {
    code: string;       // e.g. AUTH_REQUIRED / ALREADY_VOTED_OTHER / AI_LOCAL_QUOTA
    message: string;
  };
  request_id: string;   // 给后端排查；UI 里不用展示，但「复制报错信息」时建议带上
}
```
API 客户端 `client.ts` 会把 HTTP 错误统一 `throw new ApiError(code, message, requestId)`。
**业务层永远 `catch (e)` 时用 `e instanceof ApiError` 判断 `e.code` 展示本地化文案**（不要直接 `e.message` 给用户看，消息是英文的后端原始值）。

### 3.3 认证头自动注入
`AuthContext` 初始化 Supabase 会话后，`client.ts` 通过 `getAccessTokenFromAuth()` 钩子拿 token，自动加 `Authorization: Bearer <token>`。
- 社区 GET 接口：允许匿名 → 没 token 也不报错。
- 社区 POST（vote / like）、History、Settings 的 `DELETE /auth/me` → 401 触发跳转 `/login?return_to=<当前 path>`。

### 3.4 新后端接口落地的 3 步模板（以后加功能照抄）
1. **先定类型**：`src/services/api/types/xxx.ts` 写 Request / Response interface，在 `index.ts` 导出。
2. **再包门面**：`src/services/api/client.ts` 加一个具名方法（`communityFeed` / `communityThemeVote` 这种），内部调用 `api.get<T>` / `api.post<T>`，路径写 `/api/v1/...`。
3. **最后在页面用**：`useEffect(() => { void api.xxx(); }, [...])` + `useState` 存 loading/error/data。

---

## 4. 社区接口（B 阶段）—— 前端侧调用示例

```ts
import { api } from '../services/api/client';

// 1) Feed（sort 默认 latest；登录态后端自动填 liked_by_me）
const { items } = await api.communityFeed({ sort: 'hot' });

// 2) 今日 Top 榜
const { items } = await api.communityTrending();
// 跳 /nearby：nav(`/nearby?food_code=${encodeURIComponent(items[0].food_code)}`)

// 3) 主题（读）
const theme = await api.communityTheme();   // theme.voted_key 登录才不是 null

// 4) 主题投票（写，需登录）
try {
  const res = await api.communityThemeVote({ option_key: 'jp_food' });
} catch (e) {
  if (e instanceof ApiError) {
    if (e.code === 'ALREADY_VOTED_OTHER') showToast('你本周已投过别的选项哦');
    else if (e.code === 'AUTH_REQUIRED') nav('/login', { state: { return_to: '/community#theme' } });
  }
}

// 5) 点赞（写，需登录；幂等 —— duplicated=true 不会叠加数）
const { liked, likes } = await api.communityFeedLike('feed_1');
```

---

## 5. 样式与响应式约定

- 先读 `styles/tokens.css`：所有颜色 / 间距 / 圆角统一用 CSS 变量，**不许写裸色值**（除非是 `transparent` / `inherit`）。
- `page-shell` 最大宽度由 `global.css` 控制，移动端 100% 宽、桌面居中 80ch 左右。
- 社区页特殊布局：`.community-grid` 默认单栏；桌面（>900px）走 `grid-template-columns: minmax(0, 1fr) 320px;` 双栏，Top 榜用 `position: sticky; top: 1rem;` 跟着滚。
- 右下 FAB（`.community-fab`）：`bottom` 要避开移动端底部栏（`.mobile-nav` 高度 64px），所以用 `calc(var(--space-5) + 64px)`。

---

## 6. 调试小抄

| 症状 | 排查 |
|---|---|
| 社区 Feed 点 ❤️ 跳登录，登录成功没跳回社区 | 查 `/login` 页是否读取了 `location.state.return_to`；没读到就默认回 `/` |
| 活动横幅关掉刷新又出现 | 关 banner 写入 `eatwhat:campaign:dismissed-at-ms:<id>`；先看 DevTools Application → Local Storage 有没这 key / 是否 7 天过期 |
| `/community#theme` 不滚 | 锚点元素是 `<section id="community-theme">`，不是 `#theme`；CampaignBanner CTA 已经跳对了 |
| 类型报错 `Property 'session' does not exist on 'AuthContextValue'` | AuthContext 不导出 `session`；用 `isAuthenticated: boolean` 判断登录 |
| 前端 lint 报 `react-hooks/exhaustive-deps` | oxlint 是 warning，不 block build，但建议修；不要顺手 `// eslint-disable-next-line` 掉 |

---

## 7. 继续读

- 总览 & 架构：仓库根 `README.md`
- 后端契约 & 启动：`backend/README.md`（重点读「community 5 条接口速记」「9 种 final_reason」）
- 设计文档：`docs/03_EatWhat_信息架构与页面状态_v1.1_权威基线.md`（社区页结构、移动端 Tab）
