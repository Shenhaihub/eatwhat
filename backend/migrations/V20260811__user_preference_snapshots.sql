-- P6-01: 用户偏好画像快照表 user_preference_snapshots
-- 执行方式：Supabase 项目 → SQL Editor → 新建查询 → 粘贴并运行
--
-- 设计要点（与 user_recommendations 保持一致的工程约定）：
--   1. 主键 UUID gen_random_uuid()
--   2. user_id FK → auth.users(id) ON DELETE CASCADE（GDPR 删除账号级联清理）
--   3. snapshot_jsonb 存完整七维画像 + AI 追问答案（Pydantic model_dump()，版本 tag 放顶层）
--   4. RLS 双保险：后端 service_role 写操作强制 eq(user_id, ...)；RLS 作为 anon/PostgREST 直连第二道
--   5. (user_id, created_at DESC) 复合索引 → list/latest 无需排序
--   6. 医学边界：snapshot_jsonb.avoidances 只是"日常忌口"，不包含过敏医学绝对安全承诺

-- ============================================================
-- 1) TABLE
-- ============================================================

CREATE TABLE IF NOT EXISTS public.user_preference_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,

    -- 画像版本指纹（用于回滚/兼容性判断）
    questionnaire_version TEXT NOT NULL DEFAULT 'v1.0',
    dictionary_version    TEXT NOT NULL DEFAULT 'v1.0',
    snapshot_version      TEXT NOT NULL DEFAULT 'v1.0',

    -- 溯源：本次画像来自哪条推荐会话 / 历史记录
    source_session_id     TEXT,
    source_history_id     UUID REFERENCES public.user_recommendations(id) ON DELETE SET NULL,

    -- 完整画像（QuestionnaireAnswers.model_dump()）—— 字段不再拆列，
    -- 后续新维度（P6+）只需要往 JSON 里加 key，不需要 ALTER TABLE
    snapshot_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
-- 2) INDEX
-- ============================================================

-- list / latest 查询：按用户 + 时间倒序（B-tree DESC，前 20 条 = Index Only Scan）
CREATE INDEX IF NOT EXISTS idx_user_preference_snapshots_user_created
    ON public.user_preference_snapshots (user_id, created_at DESC);

-- 溯源反查：某推荐 session → 对应画像（P6-03 画像时间轴回放用）
CREATE INDEX IF NOT EXISTS idx_user_preference_snapshots_session_id
    ON public.user_preference_snapshots (source_session_id)
    WHERE source_session_id IS NOT NULL;

-- ============================================================
-- 3) RLS（第二道防线；后端已用 service_role + 强制 WHERE user_id=xxx）
-- ============================================================

ALTER TABLE public.user_preference_snapshots ENABLE ROW LEVEL SECURITY;

-- 3.1 SELECT：只能看自己的
DROP POLICY IF EXISTS preference_snapshots_select_own ON public.user_preference_snapshots;
CREATE POLICY preference_snapshots_select_own
    ON public.user_preference_snapshots FOR SELECT
    USING (auth.uid() = user_id);

-- 3.2 INSERT：只能写自己的 + 不能伪造他人 user_id
DROP POLICY IF EXISTS preference_snapshots_insert_own ON public.user_preference_snapshots;
CREATE POLICY preference_snapshots_insert_own
    ON public.user_preference_snapshots FOR INSERT
    WITH CHECK (auth.uid() = user_id);

-- 3.3 DELETE：只能删自己的
DROP POLICY IF EXISTS preference_snapshots_delete_own ON public.user_preference_snapshots;
CREATE POLICY preference_snapshots_delete_own
    ON public.user_preference_snapshots FOR DELETE
    USING (auth.uid() = user_id);

-- 3.4（可选）UPDATE：本 V1 设计不开放 UPDATE；所有写入都是 append-only 快照
--     若 P6-03 需要 UPDATE "收藏" / "标签" 字段，后续单独加 POLICY

-- ============================================================
-- 4) 自动更新 updated_at（与 user_recommendations 保持同一触发器约定）
-- ============================================================

CREATE OR REPLACE FUNCTION public.trigger_set_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS set_timestamp ON public.user_preference_snapshots;
CREATE TRIGGER set_timestamp
BEFORE UPDATE ON public.user_preference_snapshots
FOR EACH ROW EXECUTE FUNCTION public.trigger_set_timestamp();

-- ============================================================
-- 5) HOTFIX：P7 新增 snapshot_version 字段（若之前已经跑过第 1 步，需要单独补这一条）
--    首次执行整份 SQL 时 CREATE TABLE 已经包含下面字段时，ALTER 不会重复执行；
--    如果只跑了旧版 SQL（缺 snapshot_version），这一段会安全地补上。
-- ============================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = 'user_preference_snapshots'
          AND column_name  = 'snapshot_version'
    ) THEN
        ALTER TABLE public.user_preference_snapshots
            ADD COLUMN snapshot_version TEXT NOT NULL DEFAULT 'v1.0';
    END IF;
END
$$;
