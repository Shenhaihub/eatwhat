import { useEffect, useRef, useState } from 'react';

/**
 * DoodleJumpGame · 经典「往上跳」小游戏（Canvas 实现）
 *
 * 玩法：按住 ←/→（或 A/D，或触屏点按画布左右半区）控制角色左右移动，
 * 落到平台上自动向上弹跳；越跳越高（score 增大）；掉出屏幕底部即结束，可点「再玩一次」。
 * 纯前端、零依赖，用于后端冷启动等待时消磨时间。
 */

interface Platform {
  x: number;
  y: number;
  w: number;
}

interface GameState {
  player: { x: number; y: number; vx: number; vy: number; w: number; h: number };
  platforms: Platform[];
  cameraY: number;
  score: number;
  over: boolean;
}

const W = 300;
const H = 420;
const GRAVITY = 1800;
const JUMP = -760;
const MOVE_SPEED = 320;
const PLAYER_W = 24;
const PLAYER_H = 22;
const PLAT_H = 12;
const PLAT_W_MIN = 46;
const PLAT_W_MAX = 84;
const PLAT_SPACING = 52;
const PLAT_COLOR = '#7c5cff';

function rand(min: number, max: number): number {
  return Math.random() * (max - min) + min;
}

function makePlatform(y: number): Platform {
  const w = rand(PLAT_W_MIN, PLAT_W_MAX);
  const x = rand(0, W - w);
  return { x, y, w };
}

function initialPlatforms(): Platform[] {
  const list: Platform[] = [];
  for (let i = 0; i < 8; i++) {
    list.push(makePlatform(H - 40 - i * PLAT_SPACING));
  }
  return list;
}

function createInitialState(): GameState {
  const platforms = initialPlatforms();
  const first = platforms[platforms.length - 1];
  return {
    player: { x: (W - PLAYER_W) / 2, y: first.y - PLAYER_H, vx: 0, vy: JUMP, w: PLAYER_W, h: PLAYER_H },
    platforms,
    cameraY: 0,
    score: 0,
    over: false,
  };
}

export default function DoodleJumpGame() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stateRef = useRef<GameState>(createInitialState());
  const keysRef = useRef<Keys>({ left: false, right: false });
  const [score, setScore] = useState(0);
  const [over, setOver] = useState(false);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let raf = 0;
    let last = performance.now();

    const step = (now: number) => {
      const dt = Math.min(0.033, (now - last) / 1000);
      last = now;
      update(dt);
      draw(ctx, stateRef.current);
      setScore(Math.floor(stateRef.current.score));
      setOver(stateRef.current.over);
      raf = requestAnimationFrame(step);
    };

    const update = (dt: number) => {
      const s = stateRef.current;
      if (s.over) return;

      // 水平移动
      const dir = (keysRef.current.right ? 1 : 0) - (keysRef.current.left ? 1 : 0);
      s.player.x += dir * MOVE_SPEED * dt;
      if (s.player.x < 0) s.player.x = 0;
      if (s.player.x > W - s.player.w) s.player.x = W - s.player.w;

      // 垂直物理
      s.player.vy += GRAVITY * dt;
      s.player.y += s.player.vy * dt;

      // 落到平台：脚部向下穿过平台顶部时上弹
      if (s.player.vy > 0) {
        for (const p of s.platforms) {
          const playerBottom = s.player.y + s.player.h;
          const prevBottom = playerBottom - s.player.vy * dt;
          if (
            s.player.x + s.player.w > p.x &&
            s.player.x < p.x + p.w &&
            playerBottom >= p.y &&
            prevBottom <= p.y + PLAT_H
          ) {
            s.player.vy = JUMP;
            s.player.y = p.y - s.player.h;
            break;
          }
        }
      }

      // 相机跟随：玩家升到画布上 1/3 时上移世界
      const targetTop = H * 0.4;
      if (s.player.y < targetTop) {
        const shift = targetTop - s.player.y;
        s.cameraY += shift;
        s.player.y = targetTop;
        s.score += shift;
      }

      // 世界坐标越高，越往上补平台
      while (topPlatformY(s) > s.cameraY - 20) {
        s.platforms.push(makePlatform(topPlatformY(s) - PLAT_SPACING));
      }
      // 清理屏幕下方已越过的平台（避免数组无限增大）
      s.platforms = s.platforms.filter((p) => p.y < s.player.y + H + 40);

      // 掉出屏幕底部 → 结束
      if (s.player.y - s.cameraY > H) {
        s.over = true;
      }
    };

    const topPlatformY = (s: GameState): number => {
      let min = Infinity;
      for (const p of s.platforms) min = Math.min(min, p.y);
      return min;
    };

    const draw = (c: CanvasRenderingContext2D, s: GameState) => {
      c.clearRect(0, 0, W, H);
      // 背景
      c.fillStyle = '#f4f1ff';
      c.fillRect(0, 0, W, H);

      // 平台
      c.fillStyle = PLAT_COLOR;
      for (const p of s.platforms) {
        const sy = p.y - s.cameraY;
        if (sy > H) continue;
        c.fillRect(p.x, sy, p.w, PLAT_H);
      }

      // 玩家
      const px = s.player.x;
      const py = s.player.y - s.cameraY;
      c.fillStyle = '#ff6b6b';
      c.beginPath();
      c.roundRect(px, py, s.player.w, s.player.h, 6);
      c.fill();
      // 眼睛
      c.fillStyle = '#ffffff';
      c.fillRect(px + 4, py + 6, 5, 5);
      c.fillRect(px + 15, py + 6, 5, 5);
      c.fillStyle = '#222';
      c.fillRect(px + 6, py + 8, 3, 3);
      c.fillRect(px + 17, py + 8, 3, 3);
    };

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'ArrowLeft' || e.key === 'a' || e.key === 'A') {
        keysRef.current.left = true;
        e.preventDefault();
      } else if (e.key === 'ArrowRight' || e.key === 'd' || e.key === 'D') {
        keysRef.current.right = true;
        e.preventDefault();
      }
    };
    const onKeyUp = (e: KeyboardEvent) => {
      if (e.key === 'ArrowLeft' || e.key === 'a' || e.key === 'A') keysRef.current.left = false;
      else if (e.key === 'ArrowRight' || e.key === 'd' || e.key === 'D') keysRef.current.right = false;
    };

    const onPointer = (e: PointerEvent) => {
      const rect = canvas.getBoundingClientRect();
      const x = ((e.clientX - rect.left) / rect.width) * W;
      keysRef.current.left = x < W / 2;
      keysRef.current.right = x >= W / 2;
    };
    const onPointerUp = () => {
      keysRef.current.left = false;
      keysRef.current.right = false;
    };

    window.addEventListener('keydown', onKeyDown);
    window.addEventListener('keyup', onKeyUp);
    canvas.addEventListener('pointerdown', onPointer);
    window.addEventListener('pointerup', onPointerUp);

    raf = requestAnimationFrame(step);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('keyup', onKeyUp);
      canvas.removeEventListener('pointerdown', onPointer);
      window.removeEventListener('pointerup', onPointerUp);
    };
  }, []);

  const restart = () => {
    stateRef.current = createInitialState();
    keysRef.current = { left: false, right: false };
    setScore(0);
    setOver(false);
  };

  return (
    <div className="doodle-game">
      <div className="doodle-game__head">
        <span>🐰 往上跳</span>
        <span className="doodle-game__score">高度 {score}</span>
      </div>
      <canvas
        ref={canvasRef}
        width={W}
        height={H}
        className="doodle-game__canvas"
        aria-label="往上跳小游戏：按左右方向键控制角色踩平台向上跳"
        role="img"
      />
      {over && (
        <button type="button" className="doodle-game__restart" onClick={restart}>
          再玩一次
        </button>
      )}
      <p className="doodle-game__hint">←/→ 或 A/D 移动 · 点屏幕左右半区也可</p>
    </div>
  );
}

interface Keys {
  left: boolean;
  right: boolean;
}
