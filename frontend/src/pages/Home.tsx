import { Link } from 'react-router';

export default function Home() {
  return (
    <div className="page-shell placeholder-page">
      <p className="eyebrow">先定方向，再找附近</p>
      <h1>别再纠结，先决定吃什么</h1>
      <p>回答几组会根据你选择变化的问题。需要 AI 时再登录，选定食物后帮你查附近商家。</p>
      <div className="hero-actions">
        <Link to="/recommend" className="button button-primary button-large">
          开始推荐
        </Link>
        <Link to="/community" className="button button-secondary">
          看看大家在吃什么
        </Link>
      </div>
      <p className="microcopy">预设问卷无需登录 · AI 只推荐食物，不编造商家</p>

      <h2>附近商家</h2>
      <p>选定食物后，帮你查附近商家。精确坐标只用于当前搜索，不写入历史。</p>
      <div className="hero-actions">
        <Link to="/nearby" className="button button-secondary">
          选地点找商家
        </Link>
      </div>

      <h2>大家都在吃什么</h2>
      <p>社区功能建设中，后续提供明确的食物直达入口。</p>

      <h2>今日活动</h2>
      <p>活动功能建设中，后续提供活动直达附近门店。</p>
    </div>
  );
}
