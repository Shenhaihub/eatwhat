import { Link, useParams } from 'react-router';

export default function Activity() {
  const { activityId } = useParams<{ activityId: string }>();
  return (
    <div className="page-shell placeholder-page">
      <Link to="/">返回首页</Link>
      <p className="eyebrow">活动详情 · 信息以品牌官方为准</p>
      <h1>活动页</h1>
      <p>当前活动：{activityId ?? '未知'}。活动模块建设中。</p>
    </div>
  );
}
