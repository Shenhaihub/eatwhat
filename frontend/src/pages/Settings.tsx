import { Link } from 'react-router';

export default function Settings() {
  return (
    <div className="page-shell placeholder-page">
      <p className="eyebrow">设置与数据管理</p>
      <h1>我的</h1>
      <p>登录、退出、历史管理与数据删除将在账户模块（P4）接入。</p>
      <h2>说明</h2>
      <ul>
        <li>
          <Link to="/about">关于</Link>
        </li>
        <li>
          <Link to="/privacy">隐私说明</Link>
        </li>
        <li>
          <Link to="/disclaimer">免责声明</Link>
        </li>
      </ul>
    </div>
  );
}
