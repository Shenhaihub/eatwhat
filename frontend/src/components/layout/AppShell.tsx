import { Outlet } from 'react-router';
import Header from './Header';
import MobileNav from './MobileNav';

/**
 * 前端壳：桌面头部导航 + 移动端底部导航 + 主内容区。
 * 路由通过 <Outlet /> 渲染子页面。
 */
export default function AppShell() {
  return (
    <>
      <a className="skip-link" href="#main">
        跳到主要内容
      </a>
      <Header />
      <main id="main" className="page-main" tabIndex={-1}>
        <Outlet />
      </main>
      <MobileNav />
    </>
  );
}
