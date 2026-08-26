import { useEffect, useState } from 'react';
import {
  BACKEND_AWAKE_EVENT,
  BACKEND_WAKING_EVENT,
} from '../../services/api/client';
import DoodleJumpGame from './DoodleJumpGame';
import '../../styles/backend-wake.css';

/**
 * BackendWakeOverlay · 后端冷启动唤醒等待层
 *
 * 免费后端（Render Web Service）闲置 15 分钟会休眠，首次访问需 30-60s 冷启动。
 * 此覆盖层由 client.ts 的请求超时感知触发（任意请求挂起 >2s），
 * 全请求结束（BACKEND_AWAKE_EVENT）后自动关闭；等待期间展示「往上跳」小游戏消磨时间。
 */
export default function BackendWakeOverlay() {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const onWaking = () => setVisible(true);
    const onAwake = () => setVisible(false);
    window.addEventListener(BACKEND_WAKING_EVENT, onWaking);
    window.addEventListener(BACKEND_AWAKE_EVENT, onAwake);
    return () => {
      window.removeEventListener(BACKEND_WAKING_EVENT, onWaking);
      window.removeEventListener(BACKEND_AWAKE_EVENT, onAwake);
    };
  }, []);

  if (!visible) return null;

  return (
    <div
      className="backend-wake"
      role="dialog"
      aria-modal="true"
      aria-labelledby="backend-wake-title"
    >
      <div className="backend-wake__card">
        <div className="backend-wake__spinner" aria-hidden="true" />
        <h2 id="backend-wake-title">正在唤醒后端服务…</h2>
        <p className="backend-wake__desc">
          免费后端空闲 15 分钟后会休眠，重新启动需要一点时间。你可以趁这会儿玩个小游戏~
        </p>
        <p className="backend-wake__eta">
          预计等待：<strong>30-60 秒</strong> &nbsp;·&nbsp; 数据加载完成会自动关闭本页
        </p>
        <DoodleJumpGame />
      </div>
    </div>
  );
}
