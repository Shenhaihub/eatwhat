import { Link } from 'react-router';

export default function NotFound() {
  return (
    <div className="page-shell placeholder-page">
      <p className="eyebrow">404</p>
      <h1>页面不存在</h1>
      <p>你访问的页面不存在或已被移动。</p>
      <Link to="/" className="button button-primary">
        返回首页
      </Link>
    </div>
  );
}
