import { NavLink } from 'react-router';

const items = [
  { to: '/', label: '首页', icon: '⌂', end: true },
  { to: '/community', label: '社区', icon: '◉' },
  { to: '/recommend', label: '推荐', icon: '✦' },
  { to: '/settings', label: '我的', icon: '○' },
];

export default function MobileNav() {
  return (
    <nav className="mobile-nav" aria-label="移动端主导航">
      {items.map((item) => (
        <NavLink key={item.to} to={item.to} end={item.end}>
          <span aria-hidden="true">{item.icon}</span>
          {item.label}
        </NavLink>
      ))}
    </nav>
  );
}
