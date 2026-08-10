/** Supabase 前端客户端（浏览器用，只有 anon key）。
 *
 *  从 Vite 环境变量读取：
 *  - VITE_SUPABASE_URL
 *  - VITE_SUPABASE_ANON_KEY
 *  打包进 JS Bundle 没问题，它们本来就是"公开可分享"的（受 RLS 约束）。
 *
 *  🔒 绝对不要在这里写 service_role / sb_secret / PyJWT 任何后端密钥！
 */
import { createClient, type SupabaseClient } from '@supabase/supabase-js';

let _client: SupabaseClient | null = null;

export function getSupabase(): SupabaseClient {
  if (_client) return _client;
  const url = import.meta.env.VITE_SUPABASE_URL as string | undefined;
  const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY as string | undefined;
  if (!url || !anonKey) {
    throw new Error(
      'Supabase 前端配置缺失：请在 frontend/.env.local 填写 VITE_SUPABASE_URL 与 VITE_SUPABASE_ANON_KEY',
    );
  }
  _client = createClient(url, anonKey, {
    auth: {
      persistSession: true,        // localStorage 持久化 session
      autoRefreshToken: true,      // access token 过期自动刷新
      detectSessionInUrl: true,    // 自动解析 magic link 回调 URL 里的 token
      storageKey: 'ew-sb-auth',
    },
  });
  return _client;
}

/** 给测试重置用，生产代码勿调用。 */
export function _resetSupabaseClientForTests() {
  _client = null;
}
