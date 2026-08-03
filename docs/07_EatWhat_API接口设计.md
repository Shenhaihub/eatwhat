# EatWhat API 接口设计

> 2026-07-27 预算勘误：预算档位、食物预算匹配和商家价格兑现边界，以《16_EatWhat_预算档位与商家价格契约_v1.0》为准；本文中的旧预算字段或示例须在 P0-07 同步。  
> 2026-08-03 P0-07 勘误：本文与 PRD v1.2/名词表/专项契约冲突处以《22_EatWhat_P0-07_技术文档收敛清单_v1.0》为准（未登录非 AI 接口、questionnaire/next、逐题追问、固定 5 候选、规则回退非空、source_type 服务端派生等）。  
>
> 文档状态：第三阶段正式交付物  
> 产品版本：v1.0 MVP  
> 文档日期：2026-07-21  
> API 风格：REST + JSON  
> API 前缀：`/api/v1`  
> 认证方式：Supabase Auth Access Token + Bearer Header

---

# 1. 文档目的

本文档定义 EatWhat 前端与 FastAPI 后端之间的接口，包括：

- API 通用规范；
- 认证边界；
- 首页；
- 用户资料与每日额度；
- 问卷；
- 推荐会话；
- AI 补问；
- 最终推荐；
- 地点搜索；
- 附近商家；
- 公共选择；
- 推荐历史；
- 匿名共享；
- 满意度；
- 活动；
- 系统状态；
- 错误码；
- 幂等、分页和并发规则。

---

# 2. API 设计结论

## 2.1 登录注册不由 FastAPI 重复实现

以下操作由 React 直接调用 Supabase Auth：

- 邮箱注册；
- 邮箱密码登录；
- 会话刷新；
- 退出登录；
- v1.1 邮箱验证；
- v1.1 忘记密码；
- v1.1 重置密码。

FastAPI 不提供：

```text
POST /register
POST /login
```

FastAPI 只负责 EatWhat 业务。

## 2.2 业务请求认证

前端携带：

```http
Authorization: Bearer <supabase_access_token>
```

FastAPI 验证后从 Token 的 `sub` 获取用户 ID。

前端不得提交 `user_id` 来决定操作对象。

## 2.3 同步接口为主

v1.0 不引入消息队列和后台任务系统。

AI 最终推荐使用同步接口：

```text
请求
→ 额度预留
→ 调用 AI
→ 保存结果
→ 返回结果
```

接口必须配置合理超时和幂等键。

后续如模型延迟明显，再升级为异步任务。

---

# 3. 通用请求规范

## 3.1 基础 URL

本地：

```text
http://localhost:8000/api/v1
```

生产：

```text
https://api.example.com/api/v1
```

实际域名在部署阶段确定。

## 3.2 Content-Type

```http
Content-Type: application/json
```

## 3.3 字符编码

统一：

```text
UTF-8
```

## 3.4 时间格式

时间戳使用 ISO 8601：

```text
2026-07-21T10:30:00Z
```

业务日期：

```text
2026-07-21
```

## 3.5 命名风格

JSON 字段：

```text
snake_case
```

示例：

```json
{
  "remaining_ai_recommendations": 2,
  "meal_period": "lunch"
}
```

---

# 4. 通用响应规范

## 4.1 成功响应

单资源：

```json
{
  "data": {
    "id": "..."
  },
  "meta": {
    "request_id": "..."
  }
}
```

列表：

```json
{
  "data": [
    {}
  ],
  "meta": {
    "request_id": "...",
    "next_cursor": null
  }
}
```

## 4.2 错误响应

```json
{
  "error": {
    "code": "AI_PROVIDER_UNAVAILABLE",
    "message": "智能推荐暂时不可用",
    "details": null,
    "request_id": "..."
  }
}
```

## 4.3 用户信息与技术信息分离

`message`：

- 可直接展示给用户；
- 简短；
- 不含堆栈和内部凭据。

技术细节：

- 只写服务端日志；
- 通过 `request_id` 排查。

---

# 5. HTTP 状态码

| 状态码 | 场景 |
|---:|---|
| 200 | 正常读取、更新或同步操作成功 |
| 201 | 创建资源成功 |
| 204 | 删除成功且无需返回正文 |
| 400 | 请求语义错误 |
| 401 | 未登录或 Token 无效 |
| 403 | 已登录但无权访问 |
| 404 | 资源不存在 |
| 409 | 幂等冲突、状态冲突、重复共享 |
| 422 | 字段校验失败 |
| 429 | 用户额度或请求限流 |
| 502 | 第三方服务返回异常 |
| 503 | 服务暂不可用或进入降级 |
| 500 | 未预期系统错误 |

---

# 6. 认证与权限

## 6.1 公开接口

建议仅包括：

```text
GET /system/public-config
GET /activities
GET /food-categories
GET /legal/disclaimer
GET /legal/privacy
GET /health/live
GET /health/ready
```

项目介绍和法律文本也可由前端静态文件提供。

## 6.2 登录接口

除公开接口外，业务接口默认需要登录。

## 6.3 FastAPI 用户依赖

逻辑示意：

```text
读取 Authorization
→ 提取 Bearer Token
→ 验证签名、过期时间、issuer、audience
→ 获取 sub
→ 加载 profile
→ 返回 CurrentUser
```

## 6.4 资源权限

访问：

```text
/recommendations/{id}
/history/{id}
```

必须同时满足：

```text
resource.user_id == current_user.user_id
```

资源不存在和无权限可以统一返回 404，减少资源枚举风险。

---

# 7. 请求头

## 7.1 必需请求头

登录接口：

```http
Authorization: Bearer <access_token>
```

## 7.2 推荐幂等键

最终推荐接口：

```http
Idempotency-Key: <uuid>
```

同一用户、同一幂等键只能对应一个推荐生成操作。

## 7.3 请求关联 ID

前端可选传入：

```http
X-Request-ID: <uuid>
```

未提供时后端生成。

---

# 8. 首页聚合接口

## 8.1 获取首页数据

```http
GET /api/v1/home
```

认证：

```text
需要
```

响应：

```json
{
  "data": {
    "user": {
      "is_demo": false
    },
    "quota": {
      "usage_date": "2026-07-21",
      "daily_limit": 3,
      "used_count": 1,
      "reserved_count": 0,
      "remaining_count": 2,
      "resets_at": "2026-07-22T00:00:00+08:00"
    },
    "meal_period": {
      "code": "lunch",
      "display_name": "午餐",
      "can_modify": true
    },
    "activities": [],
    "community_foods": []
  },
  "meta": {
    "request_id": "..."
  }
}
```

## 8.2 设计目的

避免首页初始化时分别调用过多接口。

首页聚合接口可以并行读取：

- 额度；
- 活动；
- 公共数据；
- 当前用餐时段。

## 8.3 局部失败

某个模块失败时，不应让整个首页 500。

例如活动失败：

```json
{
  "activities": [],
  "module_status": {
    "activities": "unavailable",
    "community": "ok",
    "quota": "ok"
  }
}
```

---

# 9. 当前用户接口

## 9.1 获取当前用户业务资料

```http
GET /api/v1/me
```

响应：

```json
{
  "data": {
    "user_id": "uuid",
    "is_demo": false,
    "timezone": "Asia/Shanghai",
    "created_at": "2026-07-21T02:00:00Z"
  },
  "meta": {
    "request_id": "..."
  }
}
```

不返回：

- 密码；
- Refresh Token；
- 服务端角色；
- Secret。

## 9.2 获取额度

```http
GET /api/v1/me/ai-quota
```

响应：

```json
{
  "data": {
    "usage_date": "2026-07-21",
    "daily_limit": 3,
    "used_count": 1,
    "reserved_count": 0,
    "remaining_count": 2,
    "resets_at": "2026-07-22T00:00:00+08:00"
  }
}
```

---

# 10. 活动接口

## 10.1 获取当前活动

```http
GET /api/v1/activities?weekday=4
```

认证：

```text
可公开或登录后使用
```

响应：

```json
{
  "data": [
    {
      "id": "kfc_thursday",
      "title": "疯狂星期四",
      "subtitle": "看看附近肯德基",
      "brand_name": "肯德基",
      "search_keyword": "肯德基",
      "image_url": "/assets/activities/kfc.webp",
      "weekday": 4,
      "enabled": true,
      "disclaimer": "活动内容及参与门店以官方说明和门店实际情况为准。"
    }
  ]
}
```

活动来自服务端版本化配置。

---

# 11. 食物分类接口

## 11.1 获取启用分类

```http
GET /api/v1/food-categories
```

可选参数：

```text
meal_period
```

响应：

```json
{
  "data": [
    {
      "code": "malatang",
      "display_name": "麻辣烫",
      "aliases": ["麻辣烫"],
      "enabled": true
    }
  ]
}
```

AI 返回的 `food_code` 必须来自该词典。

---

# 12. 固定问卷接口

## 12.1 获取问卷定义

```http
GET /api/v1/questionnaire?version=v1
```

响应：

```json
{
  "data": {
    "version": "v1",
    "questions": [
      {
        "id": "appetite",
        "type": "single_select",
        "title": "今天的胃口怎么样？",
        "required": true,
        "options": [
          {
            "value": "light",
            "label": "不太想吃，想清淡少量"
          },
          {
            "value": "normal",
            "label": "正常，想吃一顿普通正餐"
          }
        ]
      }
    ]
  }
}
```

## 12.2 候选食物预览

```http
POST /api/v1/questionnaire/candidates
```

请求：

```json
{
  "questionnaire_version": "v1",
  "answers": {
    "meal_period": "lunch",
    "appetite": "normal",
    "avoidances": ["seafood"],
    "tastes": ["spicy"],
    "budget": "20_40",
    "max_distance_m": 2000
  }
}
```

响应：

```json
{
  "data": {
    "candidates": [
      {
        "food_code": "malatang",
        "display_name": "麻辣烫"
      }
    ],
    "candidate_source": "rule"
  }
}
```

该接口不调用 AI，不消耗次数。

---

# 13. 推荐会话接口概览

推荐会话分为：

```text
创建草稿
→ 保存基础答案
→ 生成补问
→ 保存补问答案
→ 最终生成
→ 查看结果
→ 查询商家
→ 完成
```

主要路由：

```text
POST   /recommendations
GET    /recommendations/{id}
PATCH  /recommendations/{id}/base-answers
POST   /recommendations/{id}/follow-up-questions
PATCH  /recommendations/{id}/follow-up-answers
POST   /recommendations/{id}/generate
POST   /recommendations/{id}/select-food
POST   /recommendations/{id}/complete
```

---

# 14. 创建推荐会话

```http
POST /api/v1/recommendations
```

请求：

```json
{
  "source_type": "ai_recommended",
  "meal_period": "lunch",
  "questionnaire_version": "v1"
}
```

`source_type` 可为：

```text
ai_recommended
user_selected
community_selected
activity_selected
```

响应：

```json
{
  "data": {
    "id": "recommendation_uuid",
    "status": "draft",
    "source_type": "ai_recommended",
    "created_at": "2026-07-21T03:00:00Z"
  }
}
```

状态码：

```text
201
```

---

# 15. 保存固定答案

```http
PATCH /api/v1/recommendations/{id}/base-answers
```

请求：

```json
{
  "answers": {
    "meal_period": "lunch",
    "appetite": "normal",
    "avoidances": ["seafood", "greasy"],
    "tastes": ["spicy", "savory"],
    "budget": "20_40",
    "max_distance_m": 2000,
    "explicit_food_code": null
  }
}
```

响应：

```json
{
  "data": {
    "id": "...",
    "status": "draft",
    "next_action": "request_follow_up"
  }
}
```

如果选择明确食物：

```json
{
  "data": {
    "id": "...",
    "status": "succeeded",
    "selected_food": {
      "food_code": "malatang",
      "display_name": "麻辣烫"
    },
    "generation_mode": "direct",
    "quota_consumed": false,
    "next_action": "select_location"
  }
}
```

---

# 16. 生成 AI 补充问题

```http
POST /api/v1/recommendations/{id}/follow-up-questions
```

请求：

```json
{
  "force_rule_fallback": false
}
```

响应：

```json
{
  "data": {
    "recommendation_id": "...",
    "status": "follow_up_ready",
    "questions": [
      {
        "id": "meal_form",
        "type": "single_select",
        "title": "今天更想吃哪种形式？",
        "options": [
          {
            "value": "soup",
            "label": "汤汤水水"
          },
          {
            "value": "rice_meal",
            "label": "干饭正餐"
          }
        ]
      }
    ],
    "generation_mode": "ai",
    "quota_consumed": false
  }
}
```

## 16.1 不需要补问

```json
{
  "data": {
    "questions": [],
    "next_action": "generate_recommendation"
  }
}
```

## 16.2 AI 失败

后端可返回规则补问：

```json
{
  "data": {
    "questions": [],
    "generation_mode": "rule",
    "next_action": "generate_recommendation",
    "warning": {
      "code": "AI_FOLLOW_UP_FALLBACK",
      "message": "智能问题暂时不可用，将使用基础答案继续推荐。"
    }
  }
}
```

补问不消耗每日额度。

---

# 17. 保存补问答案

```http
PATCH /api/v1/recommendations/{id}/follow-up-answers
```

请求：

```json
{
  "answers": {
    "meal_form": "rice_meal",
    "meal_feeling": "warm_satisfying"
  }
}
```

响应：

```json
{
  "data": {
    "status": "follow_up_ready",
    "next_action": "generate_recommendation"
  }
}
```

后端必须验证：

- 问题 ID 属于该推荐会话；
- 答案属于对应选项；
- 不允许前端写入任意 Prompt。

---

# 18. 生成最终推荐

```http
POST /api/v1/recommendations/{id}/generate
Idempotency-Key: 550e8400-e29b-41d4-a716-446655440000
```

请求：

```json
{
  "allow_rule_fallback": true
}
```

## 18.1 成功响应

```json
{
  "data": {
    "recommendation_id": "...",
    "status": "succeeded",
    "generation_mode": "ai",
    "quota_consumed": true,
    "quota": {
      "remaining_count": 2
    },
    "recommendations": [
      {
        "priority": 1,
        "food_code": "malatang",
        "display_name": "麻辣烫",
        "reason": "符合你想吃辣、预算适中并偏好热食的选择。"
      },
      {
        "priority": 2,
        "food_code": "braised_chicken_rice",
        "display_name": "黄焖鸡",
        "reason": "属于稳妥正餐，符合当前预算。"
      },
      {
        "priority": 3,
        "food_code": "small_bowl_dishes",
        "display_name": "小碗菜",
        "reason": "搭配灵活，方便避开不想吃的食材。"
      }
    ]
  },
  "meta": {
    "request_id": "..."
  }
}
```

## 18.2 规则降级

```json
{
  "data": {
    "generation_mode": "rule",
    "quota_consumed": false,
    "recommendations": [],
    "warning": {
      "code": "AI_RECOMMENDATION_FALLBACK",
      "message": "当前使用基础推荐模式，本次未消耗 AI 次数。"
    }
  }
}
```

## 18.3 额度用完

状态码：

```text
429
```

错误：

```json
{
  "error": {
    "code": "AI_DAILY_QUOTA_EXHAUSTED",
    "message": "今天的 AI 推荐次数已用完，明天会自动恢复。",
    "details": {
      "remaining_count": 0,
      "resets_at": "2026-07-22T00:00:00+08:00"
    }
  }
}
```

## 18.4 幂等重试

同一个幂等键再次请求：

- 若已成功：返回原结果；
- 若处理中：返回当前状态；
- 若失败并已释放额度：按后端重试策略处理；
- 不重复扣次数。

---

# 19. 获取推荐结果

```http
GET /api/v1/recommendations/{id}
```

用途：

- 刷新恢复；
- 从商家页返回；
- 网络中断恢复；
- 查看当前生成状态。

响应包括：

- 状态；
- 基础答案摘要；
- 三个推荐；
- 最终选择；
- 额度是否扣除；
- 共享和反馈状态。

不返回：

- 系统 Prompt；
- Provider Secret；
- 内部完整错误；
- 其他用户数据。

---

# 20. 选择最终食物

```http
POST /api/v1/recommendations/{id}/select-food
```

请求：

```json
{
  "food_code": "malatang"
}
```

规则：

- 必须是该会话推荐项；
- 或是自主选择流程中允许的分类；
- 保存 `selected_food_code`；
- 不触发地图查询；
- 不重复扣额度。

响应：

```json
{
  "data": {
    "selected_food": {
      "food_code": "malatang",
      "display_name": "麻辣烫"
    },
    "next_action": "select_location"
  }
}
```

---

# 21. 地点搜索

## 21.1 输入提示/地点搜索

```http
POST /api/v1/locations/search
```

使用 POST 的原因：

- 避免地点关键词和坐标进入普通 URL 日志；
- 请求结构更易扩展。

请求：

```json
{
  "keyword": "武汉工程大学",
  "city": "武汉市",
  "limit": 5
}
```

响应：

```json
{
  "data": [
    {
      "location_token": "short_lived_token",
      "display_name": "武汉工程大学流芳校区",
      "city_name": "武汉市",
      "district_name": "江夏区",
      "provider": "amap"
    }
  ]
}
```

## 21.2 位置 Token

建议后端不把地点搜索的内部细节长期写入数据库。

可以返回短时 `location_token`，用于随后商家搜索。

Token 内部或缓存映射：

- 经度；
- 纬度；
- 地点显示名；
- 城市；
- 区域；
- 过期时间。

## 21.3 反向地理编码

```http
POST /api/v1/locations/reverse
```

请求：

```json
{
  "latitude": 30.500000,
  "longitude": 114.400000
}
```

响应：

```json
{
  "data": {
    "location_token": "...",
    "display_name": "光谷广场附近",
    "city_name": "武汉市",
    "district_name": "洪山区"
  }
}
```

后端不得把完整精确坐标写入普通日志。

---

# 22. 演示地点

```http
GET /api/v1/locations/demo
```

响应：

```json
{
  "data": [
    {
      "code": "wuhan_optics_valley",
      "display_name": "光谷广场",
      "city_name": "武汉市",
      "district_name": "洪山区"
    }
  ]
}
```

选择演示地点：

```http
POST /api/v1/locations/demo/{code}/select
```

返回短时 `location_token`。

---

# 23. 附近商家搜索

```http
POST /api/v1/restaurants/search
```

请求：

```json
{
  "recommendation_id": "...",
  "food_code": "malatang",
  "location_token": "...",
  "radius_m": 2000,
  "limit": 5,
  "cursor": null
}
```

## 23.1 响应

```json
{
  "data": [
    {
      "provider": "amap",
      "poi_id": "B000...",
      "name": "示例麻辣烫",
      "category_text": "餐饮服务;中餐厅",
      "distance_m": 420,
      "address": "示例地址",
      "city_name": "武汉市",
      "district_name": "洪山区",
      "map_uri": "..."
    }
  ],
  "meta": {
    "next_cursor": "opaque_cursor",
    "cached": false,
    "provider_mode": "live",
    "request_id": "..."
  }
}
```

## 23.2 输入限制

- `food_code` 必须在词典内；
- `radius_m` 在允许范围；
- `limit` 最大值受控；
- `location_token` 有效且未过期；
- recommendation_id 必须属于当前用户。

## 23.3 无结果

正常返回：

```json
{
  "data": [],
  "meta": {
    "next_cursor": null
  },
  "suggestions": [
    {
      "action": "expand_radius",
      "radius_m": 3000
    },
    {
      "action": "select_other_food"
    }
  ]
}
```

不将“无结果”视为 500。

---

# 24. 完成推荐会话

```http
POST /api/v1/recommendations/{id}/complete
```

请求：

```json
{
  "merchant_search_performed": true
}
```

响应：

```json
{
  "data": {
    "status": "completed",
    "next_actions": [
      "share_anonymously",
      "submit_feedback",
      "view_history"
    ]
  }
}
```

---

# 25. 公共选择接口

## 25.1 获取公共食物

```http
GET /api/v1/community/foods?meal_period=lunch&limit=5
```

可选参数：

- `meal_period`；
- `city_code`；
- `district_code`；
- `limit`；
- `cursor`。

响应：

```json
{
  "data": [
    {
      "food_code": "malatang",
      "display_name": "麻辣烫",
      "card_type": "real_count",
      "count": 8,
      "subtitle": "今天午餐有 8 人选择"
    },
    {
      "food_code": "burger",
      "display_name": "汉堡",
      "card_type": "default_popular",
      "count": null,
      "subtitle": "今天来点轻松快捷的？"
    }
  ]
}
```

## 25.2 数据泄露限制

接口只返回聚合卡片。

不返回：

- 单个用户记录；
- 用户 ID；
- 单条区域轨迹；
- 低于阈值的人数。

---

# 26. 匿名共享接口

```http
POST /api/v1/recommendations/{id}/share
```

请求：

```json
{
  "confirmed": true
}
```

共享数据由后端从已有推荐记录构造。

前端不能提交任意：

- user_id；
- 精确位置；
- 虚构 food_code；
- 任意人数。

## 26.1 成功

```json
{
  "data": {
    "shared": true,
    "shared_at": "2026-07-21T04:00:00Z"
  }
}
```

## 26.2 重复共享

可返回原共享状态，或：

```text
409 SHARE_ALREADY_EXISTS
```

推荐采用幂等返回成功，提升体验。

## 26.3 取消共享

v1.0 可暂不提供。

如果提供：

```http
DELETE /api/v1/recommendations/{id}/share
```

则将 `is_active=false`，不一定物理删除。

---

# 27. 满意度接口

## 27.1 创建或更新反馈

```http
PUT /api/v1/recommendations/{id}/feedback
```

请求：

```json
{
  "helpful": true,
  "taste_match": "high",
  "choice_solved": "yes",
  "merchant_available": "yes",
  "suggestion": "希望增加更多面食选项"
}
```

响应：

```json
{
  "data": {
    "saved": true,
    "updated_at": "2026-07-21T04:10:00Z"
  }
}
```

使用 PUT 表示同一推荐记录只有一份当前反馈，重复提交覆盖更新。

## 27.2 字段限制

`suggestion`：

- 可选；
- 最大 1000 字符；
- 需要基础输入清理；
- 不直接渲染未经转义的 HTML。

---

# 28. 推荐历史接口

## 28.1 列表

```http
GET /api/v1/history?source_type=ai_recommended&cursor=...
```

响应：

```json
{
  "data": [
    {
      "id": "...",
      "created_at": "2026-07-21T03:00:00Z",
      "meal_period": "lunch",
      "selected_food": {
        "food_code": "malatang",
        "display_name": "麻辣烫"
      },
      "source_type": "ai_recommended",
      "shared": true,
      "helpful": true
    }
  ],
  "meta": {
    "next_cursor": null
  }
}
```

## 28.2 详情

```http
GET /api/v1/history/{id}
```

返回：

- 固定答案；
- 补问和答案；
- 三个推荐；
- 最终选择；
- 商家快照；
- 共享状态；
- 反馈。

## 28.3 删除单条

```http
DELETE /api/v1/history/{id}
```

状态码：

```text
204
```

处理：

- 删除个人推荐数据；
- 共享记录解除会话关联；
- 匿名统计可继续保留。

## 28.4 清空历史

```http
DELETE /api/v1/history
```

请求：

```json
{
  "confirmation": "CLEAR_ALL_HISTORY"
}
```

响应：

```json
{
  "data": {
    "deleted_count": 12
  }
}
```

需要二次确认，避免误操作。

---

# 29. 法律文本接口

如果不使用前端静态文件，可提供：

```text
GET /api/v1/legal/disclaimer
GET /api/v1/legal/privacy
```

响应应包含：

- 版本；
- 生效日期；
- Markdown 或结构化章节；
- 当前是否需要重新确认。

v1.0 可先使用前端版本化 Markdown。

---

# 30. 系统公开配置

```http
GET /api/v1/system/public-config
```

响应：

```json
{
  "data": {
    "app_version": "1.0.0",
    "app_mode": "live",
    "features": {
      "live_ai": true,
      "live_poi": true,
      "community": true,
      "activity_banners": true
    },
    "limits": {
      "daily_ai_recommendations": 3,
      "max_poi_radius_m": 3000
    }
  }
}
```

不返回：

- Key；
- 数据库地址；
- 内部 Provider URL；
- Secret；
- 全局成本阈值具体值。

---

# 31. 健康检查

## 31.1 存活

```http
GET /health/live
```

响应：

```json
{
  "status": "ok"
}
```

## 31.2 就绪

```http
GET /health/ready
```

响应：

```json
{
  "status": "ready",
  "database": "ok"
}
```

不要在公开接口中返回敏感依赖配置。

---

# 32. 分页规范

v1.0 推荐使用 Cursor 分页。

请求：

```text
?cursor=<opaque>&limit=20
```

响应：

```json
{
  "meta": {
    "next_cursor": "..."
  }
}
```

Cursor 不直接暴露 SQL 或敏感字段。

适用于：

- 历史；
- 商家查看更多；
- 公共选择完整页。

---

# 33. 缓存规范

后端可设置：

## 33.1 活动和食物分类

```text
Cache-Control: public, max-age=300
```

## 33.2 用户额度和历史

```text
Cache-Control: no-store
```

## 33.3 POI

服务端缓存，前端响应可保守使用：

```text
private, max-age=60
```

具体头在开发阶段验证。

---

# 34. 限流规范

## 34.1 AI

- 每用户每日成功推荐 3 次；
- 补问设短时请求频率限制；
- 最终推荐接口严格幂等；
- 全站 Provider 有硬上限。

## 34.2 POI

- 相同位置和关键词使用缓存；
- 防止快速重复查询；
- 限制半径、页数和每页数量。

## 34.3 429 响应

错误详情可以包含：

```json
{
  "retry_after_seconds": 60
}
```

同时设置：

```http
Retry-After: 60
```

---

# 35. 主要错误码

## 35.1 认证

```text
AUTH_TOKEN_MISSING
AUTH_TOKEN_INVALID
AUTH_TOKEN_EXPIRED
AUTH_PROFILE_NOT_FOUND
```

## 35.2 推荐

```text
RECOMMENDATION_NOT_FOUND
RECOMMENDATION_STATE_INVALID
RECOMMENDATION_ALREADY_COMPLETED
IDEMPOTENCY_KEY_REQUIRED
IDEMPOTENCY_CONFLICT
```

## 35.3 额度

```text
AI_DAILY_QUOTA_EXHAUSTED
AI_QUOTA_RESERVATION_FAILED
AI_QUOTA_SYNC_PENDING
```

## 35.4 AI

```text
AI_PROVIDER_UNAVAILABLE
AI_PROVIDER_TIMEOUT
AI_RESPONSE_INVALID
AI_RECOMMENDATION_FALLBACK
```

## 35.5 地点和商家

```text
LOCATION_TOKEN_INVALID
LOCATION_TOKEN_EXPIRED
LOCATION_SEARCH_EMPTY
POI_PROVIDER_UNAVAILABLE
POI_SEARCH_EMPTY
POI_RADIUS_INVALID
```

## 35.6 历史和共享

```text
HISTORY_NOT_FOUND
SHARE_ALREADY_EXISTS
SHARE_NOT_ALLOWED
FEEDBACK_INVALID
```

## 35.7 系统

```text
VALIDATION_ERROR
RATE_LIMITED
DATABASE_UNAVAILABLE
INTERNAL_ERROR
```

---

# 36. API 安全规则

1. 所有登录接口验证 Bearer Token；
2. 资源查询始终带当前 user_id；
3. 不从请求正文信任 user_id；
4. 坐标不进入普通访问日志；
5. AI/高德 Key 不返回前端；
6. 输入全部经过 Pydantic 校验；
7. 富文本输出进行转义；
8. 错误响应不包含堆栈；
9. CORS 只允许已知前端域名；
10. 生产环境使用 HTTPS；
11. 删除操作需要明确确认；
12. 最终推荐必须使用幂等键。

---

# 37. OpenAPI 文档

FastAPI 自动生成：

```text
/docs
/redoc
/openapi.json
```

生产环境建议：

- `/docs` 可关闭或限制访问；
- 或保留公开但不暴露管理接口；
- 不在 Schema 示例中放真实 Token、Key和用户数据。

---

# 38. API 测试清单

## 38.1 认证

- 无 Token；
- 过期 Token；
- 错误签名；
- 正常 Token；
- 用户访问他人资源。

## 38.2 幂等

- 同 Key 重复提交；
- 不同 Key 并发提交；
- 网络超时后重试；
- AI 成功但响应丢失。

## 38.3 额度

- 第一次、第三次、第四次；
- 次日恢复；
- AI 失败；
- 规则降级；
- 预留超时。

## 38.4 推荐

- 明确选择；
- 无明确选择；
- 无补问；
- 两个补问；
- AI 返回非法 food_code；
- AI 返回重复推荐；
- AI 超时。

## 38.5 POI

- 定位成功；
- 手动搜索；
- 演示地点；
- 无商家；
- Provider 超时；
- 缓存命中；
- 非法半径。

## 38.6 共享与历史

- 主动共享；
- 重复共享；
- 删除历史；
- 清空历史；
- 共享数据仍匿名保留；
- 用户不能访问他人历史。

---

# 39. v1.1 API 预留

未来增加：

```text
POST /account/delete-request
POST /account/delete-confirm
GET  /account/export
```

邮箱验证和密码重置仍主要使用 Supabase Auth 流程。

账户注销必须由 EatWhat 后端协调：

- 业务数据删除；
- 共享数据匿名化；
- Auth 用户删除；
- 审计记录。

---

# 40. 接口开发顺序

建议：

1. `/health/live`；
2. Token 验证依赖；
3. `/me`；
4. `/questionnaire`；
5. `/questionnaire/candidates`；
6. 推荐草稿和自主选择；
7. Mock 最终推荐；
8. Mock 商家搜索；
9. 历史；
10. Supabase 数据库；
11. 真实 AI；
12. 真实高德；
13. 公共统计；
14. 分享和反馈；
15. 限流和日志。

---

# 41. 下一步

本接口设计将与以下文档共同使用：

```text
08_EatWhat_AI推荐系统设计.md
09_EatWhat_隐私安全与免责声明.md
10_EatWhat_MVP开发计划.md
11_EatWhat_测试与验收计划.md
```
