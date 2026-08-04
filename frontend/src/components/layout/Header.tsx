import { Link, NavLink } from 'react-router';

const navItems = [
  { to: '/', label: '首页', end: true },
  { to: '/community', label: '大家在吃' },
  { to: '/recommend', label: '开始推荐' },
];

export default function Header() {
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
        <Link to="/settings" className="account-link">
          登录 / 我的
        </Link>
      </div>
    </header>
  );
}
