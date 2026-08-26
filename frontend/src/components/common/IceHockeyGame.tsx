import { useEffect, useRef, useState } from 'react';

/**
 * IceHockeyGame · 立式冰球对打（Pong 变体，Canvas 实现）
 *
 * 玩法：
 *   - 屏幕上下各有一块挡板：上方由 AI 控制，下方由你控制。
 *   - 球碰到左右边缘或挡板会反弹；漏到上边缘 → 你得分，漏到下边缘 → AI 得分，重开发球。
 *   - 发球角度随时间来回摆动，发球方在合适时机按键/按钮发出，从而「选择」发球角度。
 *   - 下方挡板用 ←/→ 或 A/D（触屏用 ◀/▶），发球用 空格/↑ 或发球按钮。
 *   - 球速用下方滑块实时调节。
 */

interface Paddle {
  x: number;
  y: number;
  w: number;
  h: number;
}

type Phase = 'serve' | 'play' | 'over';
type Side = 'player' | 'ai';

interface GameState {
  player: Paddle;
  ai: Paddle;
  ball: { x: number; y: number; vx: number; vy: number; r: number };
  phase: Phase;
  server: Side;
  serveAngle: number;
  scorePlayer: number;
  scoreAI: number;
  speed: number;
  winner: Side | null;
}

const W = 300;
const H = 400;
const PADDLE_W = 72;
const PADDLE_H = 12;
const BALL_R = 7;
const MAX_ANGLE = (60 * Math.PI) / 180; // 摆动角度 ±60°
const SWING_SPEED = 1.6; // 摆动角速度（rad/s）
const PLAYER_SPEED = 320;
const AI_SPEED = 200;
const WIN_SCORE = 7;
const AI_SERVE_TARGET = 0.55; // AI 发球角（弧度，±幅度的比例）

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

function createInitialState(): GameState {
  const player = { x: (W - PADDLE_W) / 2, y: H - 18, w: PADDLE_W, h: PADDLE_H };
  const ai = { x: (W - PADDLE_W) / 2, y: 6, w: PADDLE_W, h: PADDLE_H };
  return {
    player,
    ai,
    ball: { x: W / 2, y: H / 2, vx: 0, vy: 0, r: BALL_R },
    phase: 'serve',
    server: Math.random() < 0.5 ? 'player' : 'ai',
    serveAngle: 0,
    scorePlayer: 0,
    scoreAI: 0,
    speed: 280,
    winner: null,
  };
}

export default function IceHockeyGame() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stateRef = useRef<GameState>(createInitialState());
  const keysRef = useRef({ left: false, right: false });
  const jumpRef = useRef(false);
  const [scorePlayer, setScorePlayer] = useState(0);
  const [scoreAI, setScoreAI] = useState(0);
  const [over, setOver] = useState(false);
  const [phase, setPhase] = useState<Phase>('serve');
  const [speed, setSpeed] = useState(280);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let raf = 0;
    let last = performance.now();

    const resetServe = (s: GameState) => {
      s.phase = 'serve';
      s.server = Math.random() < 0.5 ? 'player' : 'ai';
      // 球位置在 serve 阶段每帧贴合到发球方挡板，这里只需清空速度与角度
      s.ball.vx = 0;
      s.ball.vy = 0;
      s.serveAngle = 0;
    };

    const serve = (s: GameState) => {
      // 只有 serve 阶段且轮到对应方才能发球
      if (s.phase !== 'serve') return;
      const dirY = s.server === 'player' ? -1 : 1; // 玩家在下发向上，AI 在上发向下
      const dx = Math.sin(s.serveAngle);
      const dy = dirY * Math.cos(s.serveAngle);
      const len = Math.hypot(dx, dy) || 1;
      s.ball.vx = (dx / len) * s.speed;
      s.ball.vy = (dy / len) * s.speed;
      s.phase = 'play';
    };

    const update = (dt: number) => {
      const s = stateRef.current;

      // 发球阶段：球贴到发球方挡板，摆动发球角；AI 到时机自动发
      if (s.phase === 'serve') {
        // 球吸附到发球方挡板位置（随挡板移动）
        if (s.server === 'player') {
          s.ball.x = s.player.x + s.player.w / 2;
          s.ball.y = s.player.y - s.ball.r;
        } else {
          s.ball.x = s.ai.x + s.ai.w / 2;
          s.ball.y = s.ai.y + s.ai.h + s.ball.r;
        }
        s.serveAngle = MAX_ANGLE * Math.sin(performance.now() / 1000 * SWING_SPEED);
        // AI 发球：等摆动角接近偏好角度时自动发
        if (s.server === 'ai' && Math.abs(s.serveAngle - AI_SERVE_TARGET) < 0.12) {
          serve(s);
        }
      }

      if (s.phase !== 'play') return;

      // 玩家移动
      const dir = (keysRef.current.right ? 1 : 0) - (keysRef.current.left ? 1 : 0);
      s.player.x = clamp(s.player.x + dir * PLAYER_SPEED * dt, 0, W - s.player.w);

      // AI 追踪球（球朝上时更积极）
      const targetX = s.ball.x - s.ai.w / 2;
      const aiDir = clamp(targetX - s.ai.x, -AI_SPEED * dt, AI_SPEED * dt);
      s.ai.x = clamp(s.ai.x + aiDir, 0, W - s.ai.w);

      // 球移动
      s.ball.x += s.ball.vx * dt;
      s.ball.y += s.ball.vy * dt;

      // 左右墙反弹
      if (s.ball.x - s.ball.r < 0) {
        s.ball.x = s.ball.r;
        s.ball.vx = Math.abs(s.ball.vx);
      } else if (s.ball.x + s.ball.r > W) {
        s.ball.x = W - s.ball.r;
        s.ball.vx = -Math.abs(s.ball.vx);
      }

      // 上方挡板（AI 守上边缘）：球向上撞到挡板底 → 向下弹
      const aiP = s.ai;
      if (
        s.ball.vy < 0 &&
        s.ball.y - s.ball.r < aiP.y + aiP.h &&
        s.ball.y - s.ball.r > aiP.y &&
        s.ball.x > aiP.x - s.ball.r &&
        s.ball.x < aiP.x + aiP.w + s.ball.r
      ) {
        s.ball.y = aiP.y + aiP.h + s.ball.r;
        s.ball.vy = Math.abs(s.ball.vy);
        bounceX(s, s.ball.x - (aiP.x + aiP.w / 2));
      }

      // 下方挡板（玩家守下边缘）：球向下撞到挡板顶 → 向上弹
      const pP = s.player;
      if (
        s.ball.vy > 0 &&
        s.ball.y + s.ball.r > pP.y &&
        s.ball.y + s.ball.r < pP.y + pP.h + s.ball.r &&
        s.ball.x > pP.x - s.ball.r &&
        s.ball.x < pP.x + pP.w + s.ball.r
      ) {
        s.ball.y = pP.y - s.ball.r;
        s.ball.vy = -Math.abs(s.ball.vy);
        bounceX(s, s.ball.x - (pP.x + pP.w / 2));
      }

      // 漏接判定
      if (s.ball.y - s.ball.r < 0) {
        // 越过上边缘 → 玩家得分
        s.scorePlayer += 1;
        if (s.scorePlayer >= WIN_SCORE) {
          s.phase = 'over';
          s.winner = 'player';
        } else {
          resetServe(s);
        }
      } else if (s.ball.y + s.ball.r > H) {
        // 越过下边缘 → AI 得分
        s.scoreAI += 1;
        if (s.scoreAI >= WIN_SCORE) {
          s.phase = 'over';
          s.winner = 'ai';
        } else {
          resetServe(s);
        }
      }
    };

    const bounceX = (s: GameState, offset: number): void => {
      // 依据击中挡板的相对位置改变横向速度（边缘更斜），并保持整体球速不变
      const rel = clamp(offset / (s.player.w / 2), -1, 1);
      const maxX = (s.speed * 0.7);
      s.ball.vx = clamp(rel * maxX, -maxX, maxX);
      const sp = Math.hypot(s.ball.vx, s.ball.vy) || 1;
      s.ball.vx = (s.ball.vx / sp) * s.speed;
      s.ball.vy = (s.ball.vy / sp) * s.speed;
    };

    const draw = (c: CanvasRenderingContext2D, s: GameState) => {
      c.clearRect(0, 0, W, H);
      // 背景（冰面）
      c.fillStyle = '#eaf3ff';
      c.fillRect(0, 0, W, H);
      // 中线
      c.strokeStyle = 'rgba(0,0,0,0.08)';
      c.setLineDash([6, 6]);
      c.beginPath();
      c.moveTo(0, H / 2);
      c.lineTo(W, H / 2);
      c.stroke();
      c.setLineDash([]);

      // 挡板
      c.fillStyle = '#ff6b6b';
      c.fillRect(s.player.x, s.player.y, s.player.w, s.player.h);
      c.fillStyle = '#4a7bff';
      c.fillRect(s.ai.x, s.ai.y, s.ai.w, s.ai.h);

      // 球
      c.fillStyle = '#222';
      c.beginPath();
      c.arc(s.ball.x, s.ball.y, s.ball.r, 0, Math.PI * 2);
      c.fill();

      // 发球指示箭头
      if (s.phase === 'serve') {
        const dirY = s.server === 'player' ? -1 : 1;
        const dx = Math.sin(s.serveAngle);
        const dy = dirY * Math.cos(s.serveAngle);
        const cx = W / 2;
        const cy = H / 2;
        const len = 40;
        c.strokeStyle = '#ff9f43';
        c.lineWidth = 3;
        c.beginPath();
        c.moveTo(cx, cy);
        c.lineTo(cx + dx * len, cy + dy * len);
        c.stroke();
        c.lineWidth = 1;
      }
    };

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'ArrowLeft' || e.key === 'a' || e.key === 'A') {
        keysRef.current.left = true;
        e.preventDefault();
      } else if (e.key === 'ArrowRight' || e.key === 'd' || e.key === 'D') {
        keysRef.current.right = true;
        e.preventDefault();
      } else if (e.key === 'ArrowUp' || e.key === ' ' || e.key === 'w' || e.key === 'W') {
        if (stateRef.current.server === 'player') serve(stateRef.current);
        e.preventDefault();
      }
    };
    const onKeyUp = (e: KeyboardEvent) => {
      if (e.key === 'ArrowLeft' || e.key === 'a' || e.key === 'A') keysRef.current.left = false;
      else if (e.key === 'ArrowRight' || e.key === 'd' || e.key === 'D') keysRef.current.right = false;
    };

    window.addEventListener('keydown', onKeyDown);
    window.addEventListener('keyup', onKeyUp);

    const step = (now: number) => {
      const dt = Math.min(0.033, (now - last) / 1000);
      last = now;
      if (jumpRef.current) {
        if (stateRef.current.server === 'player') serve(stateRef.current);
        jumpRef.current = false;
      }
      update(dt);
      draw(ctx, stateRef.current);
      setScorePlayer(stateRef.current.scorePlayer);
      setScoreAI(stateRef.current.scoreAI);
      setOver(stateRef.current.phase === 'over');
      setSpeed(stateRef.current.speed);
      setPhase(stateRef.current.phase);
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('keyup', onKeyUp);
    };
  }, []);

  const restart = () => {
    stateRef.current = createInitialState();
    keysRef.current = { left: false, right: false };
    jumpRef.current = false;
    setScorePlayer(0);
    setScoreAI(0);
    setOver(false);
    setPhase('serve');
    setSpeed(280);
  };

  const setGameSpeed = (v: number) => {
    stateRef.current.speed = v;
    setSpeed(v);
  };

  const press = (key: 'left' | 'right') => {
    keysRef.current[key] = true;
  };
  const release = (key: 'left' | 'right') => {
    keysRef.current[key] = false;
  };
  const playerServe = () => {
    jumpRef.current = true;
  };

  return (
    <div className="doodle-game">
      <div className="doodle-game__head">
        <span>🏒 冰球对打</span>
        <span className="doodle-game__score">
          你 {scorePlayer} : {scoreAI} AI
        </span>
      </div>
      <canvas
        ref={canvasRef}
        width={W}
        height={H}
        className="doodle-game__canvas"
        aria-label="冰球对打小游戏：控制下方挡板接球，把球打向上方避开 AI 挡板得分"
        role="img"
      />
      {phase === 'serve' && (
        <p className="doodle-game__serve-hint">
          {stateRef.current.server === 'player' ? '轮到你了，按 空格 发球（角度在摆动）' : 'AI 发球中…'}
        </p>
      )}
      {over && (
        <p className="doodle-game__serve-hint">
          {stateRef.current.winner === 'player' ? '🎉 你赢了！' : '😅 AI 赢了！'}
        </p>
      )}
      <div className="doodle-game__controls">
        <button
          type="button"
          className="doodle-game__btn"
          aria-label="向左移动"
          onPointerDown={() => press('left')}
          onPointerUp={() => release('left')}
          onPointerLeave={() => release('left')}
        >
          ◀
        </button>
        <button
          type="button"
          className="doodle-game__btn doodle-game__btn--jump"
          aria-label="发球"
          onClick={playerServe}
        >
          ⤒
        </button>
        <button
          type="button"
          className="doodle-game__btn"
          aria-label="向右移动"
          onPointerDown={() => press('right')}
          onPointerUp={() => release('right')}
          onPointerLeave={() => release('right')}
        >
          ▶
        </button>
      </div>
      <label className="doodle-game__speed">
        球速
        <input
          type="range"
          min={120}
          max={480}
          step={20}
          value={speed}
          onChange={(e) => setGameSpeed(Number(e.target.value))}
        />
        <span>{speed}</span>
      </label>
      {over && (
        <button type="button" className="doodle-game__restart" onClick={restart}>
          再玩一次
        </button>
      )}
      <p className="doodle-game__hint">←/→ 或 A/D 移动 · 空格 发球 · 先到 7 分获胜</p>
    </div>
  );
}
