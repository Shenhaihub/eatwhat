import { Link, NavLink } from 'react-router';
import { useAuth } from '../../context/AuthContext';

const navItems = [
  { to: '/', label: '首页', end: true },
  { to: '/community', label: '大家在吃' },
  { to: '/recommend', label: '开始推荐' },
];

export default function Header() {
  const { user, isAuthenticated, loading, logout } = useAuth();

  async function onLogout(e: React.MouseEvent) {
    e.preventDefault();
    await logout();
  }

  return (
    <header className="site-header">
      <div className="header-inner">
        <Link to="/" className="brand">
          <span aria-hidden="true">吃</span> EatWhat
        </Link>
        <nav className="desktop-nav" aria-label="主导航">
          {navItems.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end}>
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="header-spacer" />
        {loading ? (
          <span className="account-link account-placeholder">加载中…</span>
        ) : isAuthenticated ? (
          <div className="account-menu">
            <Link to="/settings" className="account-link">
              {user?.email ?? '我的'}
            </Link>
            <button type="button" className="logout-btn" onClick={onLogout} aria-label="退出登录">
              退出
            </button>
          </div>
        ) : (
          <Link to="/login" className="account-link">
            登录 / 我的
          </Link>
        )}
      </div>
    </header>
  );
}
