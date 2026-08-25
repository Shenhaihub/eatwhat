/**
 * P3-01：地点选择页（三种入口）+ max_distance_m 维度收集。
 *
 * 路由：/nearby
 * 三种入口（G-16：坐标不进 URL、不进历史、不进公共分享）：
 *   1) 浏览器定位：navigator.geolocation → POST /locations/reverse
 *      拒绝授权或不支持时回退到手动/演示入口，不阻塞流程。
 *   2) 手动搜索：关键词 → POST /locations/search
 *   3) 演示地点：GET /locations/demo → POST /locations/demo/{code}/select
 *
 * max_distance_m 维度（不在问卷题里收集，在地点选择页收集）：
 *   - 选项 500/1000/3000/5000 米，默认 1000 米
 *   - 选择后会和 location_token 一起用于 P3-02/P3-03 的 Mock POI 搜索
 *
 * 选中地点后页面进入"已选地点"态：
 *   - 展示 display_name + city/district + 来源标记 + 距离选择器
 *   - 显示"商家结果将在这里出现"占位（P3-03 实现）
 *   - 提供"换一个地点"按钮回到入口选择
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { useSearchParams } from 'react-router';

import { api, ApiError } from '../services/api/client';
import type {
  DemoLocationItem,
  LocationTokenInfo,
  MockMode,
  POIItem,
  RestaurantSearchResponseV1,
} from '../services/api/types';
import '../styles/nearby.css';
import { track } from '../lib/track';

type EntryMode = 'browser' | 'manual' | 'demo';

interface LoadState {
  loading: boolean;
  error: string | null;
}

const DISTANCE_OPTIONS: ReadonlyArray<{ value: number; label: string }> = [
  { value: 500, label: '500 米' },
  { value: 1000, label: '1 公里' },
  { value: 3000, label: '3 公里' },
  { value: 5000, label: '5 公里' },
];

const DEFAULT_DISTANCE_M = 1000;

const MOCK_MODE_OPTIONS: ReadonlyArray<{ value: MockMode; label: string }> = [
  { value: 'normal', label: '正常' },
  { value: 'empty', label: '空结果' },
  { value: 'slow', label: '超时' },
  { value: 'error', label: '错误' },
];

const LOCATION_TOKEN_STORAGE_KEY = 'eatwhat:location:token:v1';
const LOCATION_INFO_STORAGE_KEY = 'eatwhat:location:info:v1';
const LOCATION_DISTANCE_STORAGE_KEY = 'eatwhat:location:distance:v1';
const LOCATION_FOOD_CODE_STORAGE_KEY = 'eatwhat:location:food_code:v1';
const LOCATION_MOCK_MODE_STORAGE_KEY = 'eatwhat:location:mock_mode:v1';

interface MerchantSearchState {
  loading: boolean;
  error: string | null;
  response: RestaurantSearchResponseV1 | null;
}

interface StoredLocationInfo {
  location_token: string;
  display_name: string;
  city_name: string;
  district_name: string;
  source: EntryMode;
}

function loadStoredLocation(): {
  info: StoredLocationInfo | null;
  distance: number;
  foodCode: string;
  mockMode: MockMode;
} {
  if (typeof window === 'undefined') {
    return { info: null, distance: DEFAULT_DISTANCE_M, foodCode: '', mockMode: 'normal' };
  }
  try {
    const infoRaw = window.localStorage.getItem(LOCATION_INFO_STORAGE_KEY);
    const info = infoRaw ? (JSON.parse(infoRaw) as StoredLocationInfo) : null;
    const distRaw = window.localStorage.getItem(LOCATION_DISTANCE_STORAGE_KEY);
    const distance = distRaw ? Number(distRaw) : DEFAULT_DISTANCE_M;
    const foodCode = window.localStorage.getItem(LOCATION_FOOD_CODE_STORAGE_KEY) ?? '';
    const mockModeRaw = window.localStorage.getItem(LOCATION_MOCK_MODE_STORAGE_KEY);
    const validModes: MockMode[] = ['normal', 'empty', 'slow', 'error'];
    const mockMode: MockMode =
      mockModeRaw && validModes.includes(mockModeRaw as MockMode)
        ? (mockModeRaw as MockMode)
        : 'normal';
    if (!Number.isFinite(distance) || distance <= 0) {
      return { info, distance: DEFAULT_DISTANCE_M, foodCode, mockMode };
    }
    return { info, distance, foodCode, mockMode };
  } catch {
    return { info: null, distance: DEFAULT_DISTANCE_M, foodCode: '', mockMode: 'normal' };
  }
}

function saveLocation(
  info: StoredLocationInfo | null,
  distance: number,
  foodCode: string,
  mockMode: MockMode,
): void {
  if (typeof window === 'undefined') return;
  try {
    if (info) {
      window.localStorage.setItem(LOCATION_INFO_STORAGE_KEY, JSON.stringify(info));
    } else {
      window.localStorage.removeItem(LOCATION_INFO_STORAGE_KEY);
      window.localStorage.removeItem(LOCATION_TOKEN_STORAGE_KEY);
    }
    window.localStorage.setItem(LOCATION_DISTANCE_STORAGE_KEY, String(distance));
    window.localStorage.setItem(LOCATION_FOOD_CODE_STORAGE_KEY, foodCode);
    window.localStorage.setItem(LOCATION_MOCK_MODE_STORAGE_KEY, mockMode);
  } catch {
    // ignore (private mode / quota)
  }
}

const SOURCE_LABEL: Record<EntryMode, string> = {
  browser: '浏览器定位',
  manual: '手动搜索',
  demo: '演示地点',
};

/**
 * 商户卡片（P3-03）：
 * - 主商户（isPrimary=true）高亮显示，带"最近匹配"标记
 * - 折叠态用 hidden 属性隐藏，不卸载组件以保留 DOM 语义
 * - 地图跳转走 map_uri（高德/百度 URI Scheme），不在前端暴露坐标
 * - 距离按 km/m 自适应展示
 */
interface MerchantCardProps {
  item: POIItem;
  isPrimary: boolean;
  hidden: boolean;
}

function formatDistance(meters: number): string {
  if (meters < 1000) return `${meters} 米`;
  const km = meters / 1000;
  return km % 1 === 0 ? `${km} 公里` : `${km.toFixed(1)} 公里`;
}

function MerchantCard({ item, isPrimary, hidden }: MerchantCardProps) {
  return (
    <li
      className={`nearby-merchant-card ${isPrimary ? 'is-primary' : ''}`}
      data-testid={isPrimary ? 'merchant-primary' : `merchant-${item.poi_id}`}
      data-poi-id={item.poi_id}
      data-primary={isPrimary ? 'true' : 'false'}
      hidden={hidden || undefined}
    >
      <div className="nearby-merchant-card-header">
        <span className="nearby-merchant-name">{item.name}</span>
        {isPrimary ? (
          <span className="nearby-merchant-badge" data-testid="primary-badge">
            最近匹配
          </span>
        ) : null}
      </div>
      <p className="nearby-merchant-category">{item.category_text}</p>
      <p className="nearby-merchant-meta">
        <span className="nearby-merchant-distance" data-testid="merchant-distance">
          距离 {formatDistance(item.distance_m)}
        </span>
        <span className="nearby-merchant-separator">·</span>
        <span className="nearby-merchant-address">{item.address}</span>
      </p>
      <p className="nearby-merchant-region">
        {item.city_name} · {item.district_name}
      </p>
      <a
        href={item.map_uri}
        className="nearby-merchant-map-link"
        target="_blank"
        rel="noopener noreferrer"
        data-testid="merchant-map-link"
      >
        在地图中查看
      </a>
    </li>
  );
}

export default function Nearby() {
  const [searchParams] = useSearchParams();
  const initial = loadStoredLocation();
  // URL ?food_code=xxx 优先于 localStorage 里上次保存的 foodCode
  const urlFoodCode = searchParams.get('food_code') ?? '';
  const [selectedInfo, setSelectedInfo] = useState<StoredLocationInfo | null>(initial.info);
  const [distance, setDistance] = useState<number>(initial.distance);
  const [foodCode, setFoodCode] = useState<string>(urlFoodCode || initial.foodCode);
  const [mockMode, setMockMode] = useState<MockMode>(initial.mockMode);
  const [activeMode, setActiveMode] = useState<EntryMode>('browser');
  // 从推荐/Top榜带 food_code 过来 → 页面自动尝试一次搜索（前提：已有地点）
  const autoSearchedRef = useRef<string | null>(null);

  // ---- 浏览器定位 ----
  const [browserState, setBrowserState] = useState<LoadState>({ loading: false, error: null });

  // ---- 手动搜索 ----
  const [keyword, setKeyword] = useState('');
  const [searchState, setSearchState] = useState<LoadState>({ loading: false, error: null });
  const [searchResults, setSearchResults] = useState<readonly LocationTokenInfo[]>([]);

  // ---- 演示地点 ----
  const [demoState, setDemoState] = useState<LoadState>({ loading: false, error: null });
  const [demoItems, setDemoItems] = useState<readonly DemoLocationItem[]>([]);
  const demoLoadedRef = useRef(false);

  // ---- 商户搜索 ----
  const [merchantState, setMerchantState] = useState<MerchantSearchState>({
    loading: false,
    error: null,
    response: null,
  });
  // 1 主 + 4 折叠：展开后显示全部 5 条
  const [merchantsExpanded, setMerchantsExpanded] = useState(false);
  const merchantAbort = useRef<AbortController | null>(null);

  // ---- 持久化 ----
  useEffect(() => {
    saveLocation(selectedInfo, distance, foodCode, mockMode);
  }, [selectedInfo, distance, foodCode, mockMode]);

  // ---- 浏览器定位 ----
  const handleBrowserLocate = useCallback(async () => {
    setBrowserState({ loading: true, error: null });
    try {
      if (typeof navigator === 'undefined' || !navigator.geolocation) {
        throw new Error('当前环境不支持浏览器定位，请改用手动搜索或演示地点');
      }
      const position = await new Promise<GeolocationPosition>((resolve, reject) => {
        navigator.geolocation.getCurrentPosition(resolve, reject, {
          timeout: 8000,
          maximumAge: 60_000,
          enableHighAccuracy: false,
        });
      });
      const info = await api.locationReverse({
        latitude: position.coords.latitude,
        longitude: position.coords.longitude,
      });
      setSelectedInfo({
        location_token: info.data.location_token,
        display_name: info.data.display_name,
        city_name: info.data.city_name,
        district_name: info.data.district_name,
        source: 'browser',
      });
      setBrowserState({ loading: false, error: null });
    } catch (err) {
      const message =
        err instanceof ApiError
          ? err.message
          : err instanceof GeolocationPositionError
            ? '已拒绝定位授权，请改用手动搜索或演示地点'
            : err instanceof Error
              ? err.message
              : '定位失败，请稍后再试';
      setBrowserState({ loading: false, error: message });
    }
  }, []);

  // ---- 手动搜索 ----
  const handleSearch = useCallback(
    async (e?: FormEvent) => {
      e?.preventDefault();
      const kw = keyword.trim();
      if (!kw) {
        setSearchState({ loading: false, error: '请输入搜索关键词' });
        return;
      }
      setSearchState({ loading: true, error: null });
      try {
        const resp = await api.locationSearch({ keyword: kw, limit: 5 });
        setSearchResults(resp.data);
        setSearchState({ loading: false, error: null });
      } catch (err) {
        const message =
          err instanceof ApiError
            ? err.message
            : err instanceof Error
              ? err.message
              : '搜索失败，请稍后再试';
        setSearchState({ loading: false, error: message });
      }
    },
    [keyword],
  );

  const selectFromSearch = useCallback((info: LocationTokenInfo) => {
    setSelectedInfo({
      location_token: info.location_token,
      display_name: info.display_name,
      city_name: info.city_name,
      district_name: info.district_name,
      source: 'manual',
    });
    setSearchResults([]);
    setKeyword('');
  }, []);

  // ---- 演示地点 ----
  const loadDemo = useCallback(async () => {
    if (demoLoadedRef.current) return;
    setDemoState({ loading: true, error: null });
    try {
      const resp = await api.locationDemo();
      setDemoItems(resp.data);
      demoLoadedRef.current = true;
      setDemoState({ loading: false, error: null });
    } catch (err) {
      const message =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : '演示地点加载失败';
      setDemoState({ loading: false, error: message });
    }
  }, []);

  useEffect(() => {
    if (activeMode === 'demo' && !demoLoadedRef.current) {
      void loadDemo();
    }
  }, [activeMode, loadDemo]);

  const selectDemo = useCallback(
    async (code: string) => {
      setSearchState({ loading: false, error: null });
      setBrowserState({ loading: false, error: null });
      try {
        const resp = await api.locationDemoSelect(code);
        setSelectedInfo({
          location_token: resp.data.location_token,
          display_name: resp.data.display_name,
          city_name: resp.data.city_name,
          district_name: resp.data.district_name,
          source: 'demo',
        });
      } catch (err) {
        const message =
          err instanceof ApiError
            ? err.message
            : err instanceof Error
              ? err.message
              : '选择演示地点失败';
        setDemoState({ loading: false, error: message });
      }
    },
    [],
  );

  // ---- 商户搜索 ----
  const handleSearchMerchants = useCallback(
    async (e?: FormEvent) => {
      e?.preventDefault();
      if (!selectedInfo) return;
      const fc = foodCode.trim();
      if (!fc) {
        setMerchantState({ loading: false, error: '请输入食物 code（例如 malatang）', response: null });
        return;
      }
      if (merchantAbort.current) merchantAbort.current.abort();
      const controller = new AbortController();
      merchantAbort.current = controller;
      setMerchantState({ loading: true, error: null, response: null });
      setMerchantsExpanded(false);
      try {
        const resp = await api.restaurantsSearch(
          {
            food_code: fc,
            location_token: selectedInfo.location_token,
            radius_m: distance,
            limit: 5,
            mock_mode: mockMode,
          },
          { signal: controller.signal },
        );
        setMerchantState({ loading: false, error: null, response: resp });
      } catch (err) {
        if (controller.signal.aborted) return;
        const message =
          err instanceof ApiError
            ? err.message
            : err instanceof Error
              ? err.message
              : '商户搜索失败，请稍后再试';
        setMerchantState({ loading: false, error: message, response: null });
      }
    },
    [selectedInfo, foodCode, distance, mockMode],
  );

  // ---- 从推荐/Top 榜跳过来时，URL 上带着 food_code：
  //   - 如果已经保存过地点，自动搜一次
  //   - 否则等用户选完地点后再自动触发一次
  // 注意："手动在输入框里敲 food_code" 不自动搜，避免和用户点击「搜索商家」按钮的测试/交互冲突。
  useEffect(() => {
    const fc = urlFoodCode.trim() || foodCode.trim(); // 仅当 urlFoodCode 或 foodCode 由其他地方（非输入）改变才触发时有限制，下面用"is set from url"判据
    if (!fc) return;
    if (!selectedInfo) return;
    // 只有当 foodCode === urlFoodCode（即当前显示的 food 来自 URL），才允许自动搜
    if (foodCode.trim() !== urlFoodCode.trim()) return;
    const key = `${fc}|${selectedInfo.location_token}`;
    if (autoSearchedRef.current === key) return;
    autoSearchedRef.current = key;
    // C：URL food_code → 预填 + 自动搜 成功一次打一个 applied，
    // 方便算「社区/推荐页点"去吃"→ Nearby 自动搜 成功」的漏斗转化率
    track('nearby.url_foodcode_applied', {
      food_code: fc,
      location_token: selectedInfo.location_token,
      distance_km: distance,
    });
    void handleSearchMerchants();
  }, [selectedInfo, foodCode, urlFoodCode, handleSearchMerchants, distance]);

  const handleResetLocation = useCallback(() => {
    setSelectedInfo(null);
    setBrowserState({ loading: false, error: null });
    setSearchState({ loading: false, error: null });
    setSearchResults([]);
    setKeyword('');
    setMerchantState({ loading: false, error: null, response: null });
    setMerchantsExpanded(false);
    if (merchantAbort.current) {
      merchantAbort.current.abort();
      merchantAbort.current = null;
    }
  }, []);

  // ============ 渲染 ============
  if (selectedInfo) {
    return (
      <div className="page-shell nearby-page">
        <p className="eyebrow">已选地点</p>
        <h1>{selectedInfo.display_name}</h1>
        <p className="microcopy">
          {selectedInfo.city_name} · {selectedInfo.district_name} · 来源：
          {SOURCE_LABEL[selectedInfo.source]}
        </p>

        <div className="notice nearby-privacy-notice" aria-label="隐私提示">
          精确坐标只用于当前商家搜索，不写入历史与公共分享。
        </div>

        <fieldset className="q-card nearby-distance-card">
          <legend>
            <span className="q-title">搜索范围</span>
            <span className="q-hint">单选 · 决定附近商家搜索半径</span>
          </legend>
          <div className="q-options single_choice">
            {DISTANCE_OPTIONS.map((opt) => {
              const selected = distance === opt.value;
              return (
                <button
                  key={opt.value}
                  type="button"
                  className={`q-option ${selected ? 'is-selected' : ''}`}
                  onClick={() => setDistance(opt.value)}
                  aria-pressed={selected}
                  data-testid={`distance-option-${opt.value}`}
                >
                  <span className="q-option-label">{opt.label}</span>
                </button>
              );
            })}
          </div>
        </fieldset>

        <form onSubmit={handleSearchMerchants} className="nearby-merchant-search-form">
          <label className="sr-only" htmlFor="nearby-food-code">
            食物 code
          </label>
          <input
            id="nearby-food-code"
            type="text"
            value={foodCode}
            onChange={(e) => setFoodCode(e.target.value)}
            placeholder="食物 code（例如 malatang / beef_noodles）"
            maxLength={32}
            autoComplete="off"
            data-testid="food-code-input"
          />
          <button
            type="submit"
            className="button button-primary"
            disabled={merchantState.loading}
            data-testid="search-merchants-btn"
          >
            {merchantState.loading ? '搜索中…' : '搜附近商家'}
          </button>
        </form>

        {/* 测试模式选择器（P3-02 四态可重复触发） */}
        <fieldset className="q-card nearby-mock-mode-card">
          <legend>
            <span className="q-title">测试模式</span>
            <span className="q-hint">单选 · Mock POI 四态切换（仅 mock provider 生效）</span>
          </legend>
          <div className="q-options single_choice">
            {MOCK_MODE_OPTIONS.map((opt) => {
              const selected = mockMode === opt.value;
              return (
                <button
                  key={opt.value}
                  type="button"
                  className={`q-option ${selected ? 'is-selected' : ''}`}
                  onClick={() => setMockMode(opt.value)}
                  aria-pressed={selected}
                  data-testid={`mock-mode-${opt.value}`}
                >
                  <span className="q-option-label">{opt.label}</span>
                </button>
              );
            })}
          </div>
        </fieldset>

        {/* 商户搜索结果 */}
        <section
          className="nearby-merchant-results"
          aria-label="附近商家结果"
          data-testid="merchant-results"
        >
          {merchantState.loading ? (
            <div className="loading-row" aria-live="polite">
              正在搜索附近商家…
            </div>
          ) : null}

          {merchantState.error ? (
            <div className="notice error-notice" role="alert" data-testid="merchant-error">
              <strong>商户搜索失败：</strong>
              <span>{merchantState.error}</span>
              <button
                type="button"
                className="button button-secondary"
                onClick={() => void handleSearchMerchants()}
                style={{ marginLeft: 'var(--space-3)' }}
              >
                再试一次
              </button>
            </div>
          ) : null}

          {merchantState.response ? (
            <>
              {/* 来源标注 */}
              <p className="microcopy nearby-source-attribution" data-testid="source-attribution">
                数据来源：{merchantState.response.meta.provider_mode === 'mock' ? 'Mock 演示数据' : '高德地图'}
                {merchantState.response.meta.cached ? ' · 命中缓存' : ''}
              </p>

              {merchantState.response.data.length === 0 ? (
                <div className="notice nearby-empty-notice" data-testid="merchant-empty">
                  <p>未在 {distance} 米内找到匹配商家。</p>
                  {merchantState.response.suggestions.length > 0 ? (
                    <ul className="nearby-suggestions">
                      {merchantState.response.suggestions.map((s, idx) => (
                        <li key={`${s.action}-${idx}`}>
                          {s.action === 'expand_radius' && s.radius_m ? (
                            <button
                              type="button"
                              className="button button-secondary"
                              onClick={() => {
                                setDistance(s.radius_m!);
                                void handleSearchMerchants();
                              }}
                            >
                              扩大搜索范围到 {s.radius_m} 米
                            </button>
                          ) : (
                            <span className="microcopy">试试换一种食物</span>
                          )}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              ) : (
                <>
                  <ul className="nearby-merchant-list" data-testid="merchant-list">
                    {merchantState.response.data.map((item, idx) => (
                      <MerchantCard
                        key={item.poi_id}
                        item={item}
                        isPrimary={idx === 0}
                        hidden={!merchantsExpanded && idx > 0}
                      />
                    ))}
                  </ul>
                  {!merchantsExpanded && merchantState.response.data.length > 1 ? (
                    <button
                      type="button"
                      className="button button-secondary button-large expand-merchants"
                      data-testid="expand-merchants"
                      onClick={() => setMerchantsExpanded(true)}
                    >
                      查看其他 {merchantState.response.data.length - 1} 家备选
                    </button>
                  ) : null}
                </>
              )}
            </>
          ) : !merchantState.loading && !merchantState.error ? (
            <div className="nearby-merchant-placeholder" data-testid="merchant-placeholder">
              <p className="microcopy">
                输入食物 code 并点击"搜附近商家"，结果按距离升序展示（最近匹配优先）。
              </p>
            </div>
          ) : null}
        </section>

        <div className="q-footer">
          <button
            type="button"
            className="button button-secondary"
            data-testid="reset-location"
            onClick={handleResetLocation}
          >
            换一个地点
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="page-shell nearby-page">
      <p className="eyebrow">地点与商家</p>
      <h1>先选一个地点</h1>
      <p className="microcopy">
        精确坐标只用于当前搜索，不会写入历史；拒绝浏览器定位也能用手动搜索或演示地点。
      </p>

      <div className="nearby-tabs" role="tablist" aria-label="地点入口选择">
        {(
          [
            { id: 'browser', label: '浏览器定位' },
            { id: 'manual', label: '手动搜索' },
            { id: 'demo', label: '演示地点' },
          ] as const
        ).map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            id={`nearby-tab-${tab.id}`}
            aria-selected={activeMode === tab.id}
            aria-controls={`nearby-panel-${tab.id}`}
            className={`nearby-tab ${activeMode === tab.id ? 'is-active' : ''}`}
            onClick={() => setActiveMode(tab.id)}
            data-testid={`tab-${tab.id}`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* ====== 浏览器定位 ====== */}
      <section
        id="nearby-panel-browser"
        role="tabpanel"
        aria-labelledby="nearby-tab-browser"
        hidden={activeMode !== 'browser'}
        data-testid="panel-browser"
      >
        <div className="nearby-panel-inner">
          <p className="microcopy">
            点击下方按钮授权浏览器获取当前位置。坐标系为 WGS84，后端转换为 GCJ-02 后只存内存。
          </p>
          <button
            type="button"
            className="button button-primary button-large"
            onClick={() => void handleBrowserLocate()}
            disabled={browserState.loading}
            data-testid="browser-locate-btn"
          >
            {browserState.loading ? '正在定位…' : '使用浏览器定位'}
          </button>
          {browserState.error ? (
            <div className="notice error-notice" role="alert">
              {browserState.error}
            </div>
          ) : null}
        </div>
      </section>

      {/* ====== 手动搜索 ====== */}
      <section
        id="nearby-panel-manual"
        role="tabpanel"
        aria-labelledby="nearby-tab-manual"
        hidden={activeMode !== 'manual'}
        data-testid="panel-manual"
      >
        <div className="nearby-panel-inner">
          <form onSubmit={handleSearch} className="nearby-search-form">
            <label className="sr-only" htmlFor="nearby-search-keyword">
              搜索关键词
            </label>
            <input
              id="nearby-search-keyword"
              type="text"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              placeholder="例如：光谷、江汉路"
              maxLength={64}
              autoComplete="off"
              data-testid="search-keyword-input"
            />
            <button
              type="submit"
              className="button button-primary"
              disabled={searchState.loading}
              data-testid="search-submit"
            >
              {searchState.loading ? '搜索中…' : '搜索'}
            </button>
          </form>
          {searchState.error ? (
            <div className="notice error-notice" role="alert">
              {searchState.error}
            </div>
          ) : null}
          {searchResults.length > 0 ? (
            <ul className="nearby-result-list" data-testid="search-results">
              {searchResults.map((item) => (
                <li key={item.location_token}>
                  <button
                    type="button"
                    className="nearby-result-item"
                    onClick={() => selectFromSearch(item)}
                  >
                    <span className="nearby-result-name">{item.display_name}</span>
                    <span className="nearby-result-meta">
                      {item.city_name} · {item.district_name}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : searchState.loading === false && searchState.error === null && keyword ? (
            <p className="microcopy">未找到匹配地点，试试演示地点入口。</p>
          ) : null}
        </div>
      </section>

      {/* ====== 演示地点 ====== */}
      <section
        id="nearby-panel-demo"
        role="tabpanel"
        aria-labelledby="nearby-tab-demo"
        hidden={activeMode !== 'demo'}
        data-testid="panel-demo"
      >
        <div className="nearby-panel-inner">
          <p className="microcopy">
            适合不方便授权定位或想快速演示的场景。预设 5 个武汉常见地点。
          </p>
          {demoState.error ? (
            <div className="notice error-notice" role="alert">
              {demoState.error}
            </div>
          ) : null}
          {demoState.loading ? (
            <div className="loading-row" aria-live="polite">
              正在加载演示地点…
            </div>
          ) : (
            <ul className="nearby-result-list" data-testid="demo-list">
              {demoItems.map((item) => (
                <li key={item.code}>
                  <button
                    type="button"
                    className="nearby-result-item"
                    onClick={() => void selectDemo(item.code)}
                    data-testid={`demo-select-${item.code}`}
                  >
                    <span className="nearby-result-name">{item.display_name}</span>
                    <span className="nearby-result-meta">
                      {item.city_name} · {item.district_name}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>
    </div>
  );
}
