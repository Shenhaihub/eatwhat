import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as apiClient from '../services/api/client';
import type {
  DemoLocationItem,
  DemoLocationListResponse,
  DemoLocationSelectResponse,
  LocationReverseResponseV1,
  LocationSearchResponseV1,
  LocationTokenInfo,
  POIItem,
  RestaurantSearchResponseV1,
} from '../services/api/types';
import Nearby from '../pages/Nearby';

const DEMO_ITEMS: DemoLocationItem[] = [
  {
    code: 'wuhan_optics_valley',
    display_name: '光谷广场',
    city_name: '武汉市',
    district_name: '洪山区',
  },
  {
    code: 'wuhan_jianghan_road',
    display_name: '江汉路步行街',
    city_name: '武汉市',
    district_name: '江汉区',
  },
];

function makeTokenInfo(displayName: string, districtName: string, tokenSeed = 'a'): LocationTokenInfo {
  return {
    location_token: tokenSeed.repeat(32),
    display_name: displayName,
    city_name: '武汉市',
    district_name: districtName,
  };
}

describe('/nearby 地点选择页（P3-01）', () => {
  beforeEach(() => {
    if (typeof window !== 'undefined') {
      window.localStorage.clear();
    }
  });

  afterEach(() => {
    vi.restoreAllMocks();
    if (typeof window !== 'undefined') {
      window.localStorage.clear();
    }
  });

  it('1) 演示地点入口：GET /locations/demo → 渲染 5 条结果 → 点击选择 → 进入"已选地点"态', async () => {
    const user = userEvent.setup();
    const demoSpy = vi
      .spyOn(apiClient.api, 'locationDemo')
      .mockResolvedValue({ data: DEMO_ITEMS } as DemoLocationListResponse);
    const selectSpy = vi
      .spyOn(apiClient.api, 'locationDemoSelect')
      .mockResolvedValue({
        data: makeTokenInfo('光谷广场', '洪山区'),
      } as DemoLocationSelectResponse);

    render(<Nearby />);

    // 默认 tab 是 browser，先切到 demo
    await user.click(screen.getByTestId('tab-demo'));

    await waitFor(() => expect(demoSpy).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId('demo-select-wuhan_optics_valley')).toBeInTheDocument();
    expect(screen.getByTestId('demo-select-wuhan_jianghan_road')).toBeInTheDocument();

    // 点击第一个演示地点
    await user.click(screen.getByTestId('demo-select-wuhan_optics_valley'));
    expect(selectSpy).toHaveBeenCalledWith('wuhan_optics_valley');

    // 进入已选地点态
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: '光谷广场' })).toBeInTheDocument(),
    );
    expect(screen.getByText(/武汉市 · 洪山区 · 来源：演示地点/)).toBeInTheDocument();
    expect(screen.getByTestId('merchant-placeholder')).toBeInTheDocument();
  });

  it('2) 手动搜索：输入关键词 → POST /locations/search → 渲染结果 → 点击 → 已选地点态', async () => {
    const user = userEvent.setup();
    const searchSpy = vi
      .spyOn(apiClient.api, 'locationSearch')
      .mockResolvedValue({
        data: [
          makeTokenInfo('光谷广场', '洪山区', 'a'),
          makeTokenInfo('江汉路步行街', '江汉区', 'b'),
        ],
      } as LocationSearchResponseV1);

    render(<Nearby />);

    await user.click(screen.getByTestId('tab-manual'));
    await user.type(screen.getByTestId('search-keyword-input'), '光谷');
    await user.click(screen.getByTestId('search-submit'));

    await waitFor(() => expect(searchSpy).toHaveBeenCalledTimes(1));
    const req = searchSpy.mock.calls[0]?.[0];
    expect(req?.keyword).toBe('光谷');

    // 渲染两条结果
    const results = screen.getByTestId('search-results');
    expect(results.querySelectorAll('.nearby-result-item').length).toBe(2);

    // 点击第一条 → 已选地点态
    await user.click(screen.getAllByRole('button', { name: /光谷广场/ })[0]);
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: '光谷广场' })).toBeInTheDocument(),
    );
    expect(screen.getByText(/来源：手动搜索/)).toBeInTheDocument();
  });

  it('3) 手动搜索：空关键词 → 不发请求，显示错误提示', async () => {
    const user = userEvent.setup();
    const searchSpy = vi.spyOn(apiClient.api, 'locationSearch');

    render(<Nearby />);
    await user.click(screen.getByTestId('tab-manual'));
    await user.click(screen.getByTestId('search-submit'));

    expect(searchSpy).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent('请输入搜索关键词');
  });

  it('4) 浏览器定位：mock geolocation 成功 → POST /locations/reverse → 已选地点态', async () => {
    const user = userEvent.setup();
    const reverseSpy = vi
      .spyOn(apiClient.api, 'locationReverse')
      .mockResolvedValue({
        data: makeTokenInfo('光谷广场', '洪山区'),
      } as LocationReverseResponseV1);

    // mock navigator.geolocation
    const geoMock = {
      getCurrentPosition: vi.fn(
        (success: (pos: GeolocationPosition) => void) => {
          success({
            coords: { latitude: 30.5, longitude: 114.4, accuracy: 10 } as GeolocationCoordinates,
            timestamp: Date.now(),
          } as GeolocationPosition);
        },
      ),
    };
    Object.defineProperty(globalThis, 'navigator', {
      value: { ...globalThis.navigator, geolocation: geoMock },
      writable: true,
      configurable: true,
    });

    render(<Nearby />);
    await user.click(screen.getByTestId('browser-locate-btn'));

    await waitFor(() => expect(reverseSpy).toHaveBeenCalledTimes(1));
    const req = reverseSpy.mock.calls[0]?.[0];
    expect(req?.latitude).toBe(30.5);
    expect(req?.longitude).toBe(114.4);

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: '光谷广场' })).toBeInTheDocument(),
    );
    expect(screen.getByText(/来源：浏览器定位/)).toBeInTheDocument();
  });

  it('5) 已选地点态：可切换 max_distance_m，并持久化到 localStorage', async () => {
    const user = userEvent.setup();
    vi.spyOn(apiClient.api, 'locationDemo').mockResolvedValue({
      data: DEMO_ITEMS,
    } as DemoLocationListResponse);
    vi.spyOn(apiClient.api, 'locationDemoSelect').mockResolvedValue({
      data: makeTokenInfo('光谷广场', '洪山区'),
    } as DemoLocationSelectResponse);

    render(<Nearby />);
    await user.click(screen.getByTestId('tab-demo'));
    await waitFor(() => expect(screen.getByTestId('demo-list')).toBeInTheDocument());
    await user.click(screen.getByTestId('demo-select-wuhan_optics_valley'));

    // 进入已选地点态，默认 1000m
    await waitFor(() => expect(screen.getByTestId('distance-option-1000')).toHaveClass('is-selected'));

    // 切换到 3000m
    await user.click(screen.getByTestId('distance-option-3000'));
    expect(screen.getByTestId('distance-option-3000')).toHaveClass('is-selected');

    // localStorage 持久化
    expect(window.localStorage.getItem('eatwhat:location:distance:v1')).toBe('3000');
    const storedInfo = window.localStorage.getItem('eatwhat:location:info:v1');
    expect(storedInfo).toContain('"display_name":"光谷广场"');
  });

  it('6) 已选地点态：点击"换一个地点"回到入口选择', async () => {
    const user = userEvent.setup();
    vi.spyOn(apiClient.api, 'locationDemo').mockResolvedValue({
      data: DEMO_ITEMS,
    } as DemoLocationListResponse);
    vi.spyOn(apiClient.api, 'locationDemoSelect').mockResolvedValue({
      data: makeTokenInfo('光谷广场', '洪山区'),
    } as DemoLocationSelectResponse);

    render(<Nearby />);
    await user.click(screen.getByTestId('tab-demo'));
    await waitFor(() => expect(screen.getByTestId('demo-list')).toBeInTheDocument());
    await user.click(screen.getByTestId('demo-select-wuhan_optics_valley'));

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: '光谷广场' })).toBeInTheDocument(),
    );

    await user.click(screen.getByTestId('reset-location'));

    // 回到入口态
    expect(screen.getByRole('heading', { name: '先选一个地点' })).toBeInTheDocument();
  });
});

// ============ P3-03 商户结果页 ============

function makePOIItem(poiId: string, name: string, distanceM: number, idx: number): POIItem {
  return {
    provider: 'mock',
    poi_id: poiId,
    name,
    category_text: '快餐 · 川菜',
    distance_m: distanceM,
    address: `武汉市洪山区光谷街 ${idx + 1} 号`,
    city_name: '武汉市',
    district_name: '洪山区',
    map_uri: `https://uri.amap.com/marker?name=${encodeURIComponent(name)}`,
  };
}

function makeMerchantResponse(count: number): RestaurantSearchResponseV1 {
  const items: POIItem[] = Array.from({ length: count }, (_, i) =>
    makePOIItem(`poi-${i + 1}`, `麻辣烫 ${i + 1} 号店`, 200 + i * 150, i),
  );
  return {
    data: items,
    meta: { next_cursor: null, cached: false, provider_mode: 'mock', request_id: 'req-test' },
    suggestions: [],
  };
}

async function setupSelectedLocation(user: ReturnType<typeof userEvent.setup>) {
  vi.spyOn(apiClient.api, 'locationDemo').mockResolvedValue({
    data: DEMO_ITEMS,
  } as DemoLocationListResponse);
  vi.spyOn(apiClient.api, 'locationDemoSelect').mockResolvedValue({
    data: makeTokenInfo('光谷广场', '洪山区'),
  } as DemoLocationSelectResponse);

  render(<Nearby />);
  await user.click(screen.getByTestId('tab-demo'));
  await waitFor(() => expect(screen.getByTestId('demo-list')).toBeInTheDocument());
  await user.click(screen.getByTestId('demo-select-wuhan_optics_valley'));
  await waitFor(() =>
    expect(screen.getByRole('heading', { name: '光谷广场' })).toBeInTheDocument(),
  );
}

describe('/nearby 商户结果页（P3-03）', () => {
  beforeEach(() => {
    if (typeof window !== 'undefined') {
      window.localStorage.clear();
    }
  });

  afterEach(() => {
    vi.restoreAllMocks();
    if (typeof window !== 'undefined') {
      window.localStorage.clear();
    }
  });

  it('7) 正常搜索：输入 food_code → 返回 5 条 → 主商户可见 + 4 折叠 + 来源标注', async () => {
    const user = userEvent.setup();
    await setupSelectedLocation(user);

    const searchSpy = vi
      .spyOn(apiClient.api, 'restaurantsSearch')
      .mockResolvedValue(makeMerchantResponse(5));

    await user.type(screen.getByTestId('food-code-input'), 'malatang');
    await user.click(screen.getByTestId('search-merchants-btn'));

    await waitFor(() => expect(searchSpy).toHaveBeenCalledTimes(1));
    const req = searchSpy.mock.calls[0]?.[0];
    expect(req?.food_code).toBe('malatang');
    expect(req?.radius_m).toBe(1000);
    expect(req?.limit).toBe(5);
    expect(req?.mock_mode).toBe('normal');

    // 来源标注
    expect(screen.getByTestId('source-attribution')).toHaveTextContent('Mock 演示数据');

    // 主商户可见
    const list = screen.getByTestId('merchant-list');
    const allCards = list.querySelectorAll('.nearby-merchant-card');
    expect(allCards).toHaveLength(5);
    const visibleCards = list.querySelectorAll('.nearby-merchant-card:not([hidden])');
    expect(visibleCards).toHaveLength(1);
    expect(screen.getByTestId('merchant-primary')).toBeInTheDocument();
    expect(screen.getByTestId('primary-badge')).toHaveTextContent('最近匹配');

    // 折叠按钮
    expect(screen.getByTestId('expand-merchants')).toHaveTextContent('查看其他 4 家备选');
  });

  it('8) 展开：点击"查看其他" → 全部 5 条可见，按钮消失', async () => {
    const user = userEvent.setup();
    await setupSelectedLocation(user);

    vi.spyOn(apiClient.api, 'restaurantsSearch').mockResolvedValue(makeMerchantResponse(5));

    await user.type(screen.getByTestId('food-code-input'), 'malatang');
    await user.click(screen.getByTestId('search-merchants-btn'));
    await waitFor(() => expect(screen.getByTestId('merchant-list')).toBeInTheDocument());

    const list = screen.getByTestId('merchant-list');
    expect(list.querySelectorAll('.nearby-merchant-card:not([hidden])')).toHaveLength(1);

    await user.click(screen.getByTestId('expand-merchants'));

    expect(list.querySelectorAll('.nearby-merchant-card:not([hidden])')).toHaveLength(5);
    expect(screen.queryByTestId('expand-merchants')).toBeNull();
  });

  it('9) 空结果：mock_mode=empty → 显示空提示 + 扩大范围建议', async () => {
    const user = userEvent.setup();
    await setupSelectedLocation(user);

    const emptyResponse: RestaurantSearchResponseV1 = {
      data: [],
      meta: { next_cursor: null, cached: false, provider_mode: 'mock', request_id: 'req-empty' },
      suggestions: [{ action: 'expand_radius', radius_m: 3000 }],
    };
    vi.spyOn(apiClient.api, 'restaurantsSearch').mockResolvedValue(emptyResponse);

    // 切换 mock_mode 到 empty
    await user.click(screen.getByTestId('mock-mode-empty'));

    await user.type(screen.getByTestId('food-code-input'), 'malatang');
    await user.click(screen.getByTestId('search-merchants-btn'));

    await waitFor(() => expect(screen.getByTestId('merchant-empty')).toBeInTheDocument());
    expect(screen.getByTestId('merchant-empty')).toHaveTextContent('未在 1000 米内找到匹配商家');

    // 建议按钮
    const expandBtn = screen.getByRole('button', { name: /扩大搜索范围到 3000 米/ });
    expect(expandBtn).toBeInTheDocument();
  });

  it('10) 错误态：mock_mode=error → 显示错误提示 + 重试按钮', async () => {
    const user = userEvent.setup();
    await setupSelectedLocation(user);

    const { ApiError } = await import('../services/api/client');
    vi.spyOn(apiClient.api, 'restaurantsSearch').mockRejectedValue(
      new ApiError(503, 'Mock POI 服务模拟不可用', 'SERVICE_UNAVAILABLE'),
    );

    await user.click(screen.getByTestId('mock-mode-error'));
    await user.type(screen.getByTestId('food-code-input'), 'malatang');
    await user.click(screen.getByTestId('search-merchants-btn'));

    await waitFor(() => expect(screen.getByTestId('merchant-error')).toBeInTheDocument());
    expect(screen.getByTestId('merchant-error')).toHaveTextContent('商户搜索失败');
  });

  it('11) 空 food_code：不发送请求，显示错误提示', async () => {
    const user = userEvent.setup();
    await setupSelectedLocation(user);

    const searchSpy = vi.spyOn(apiClient.api, 'restaurantsSearch');

    await user.click(screen.getByTestId('search-merchants-btn'));

    expect(searchSpy).not.toHaveBeenCalled();
    expect(screen.getByTestId('merchant-error')).toHaveTextContent('请输入食物 code');
  });

  it('12) mock_mode 持久化：切换后写入 localStorage', async () => {
    const user = userEvent.setup();
    await setupSelectedLocation(user);

    await user.click(screen.getByTestId('mock-mode-slow'));
    expect(window.localStorage.getItem('eatwhat:location:mock_mode:v1')).toBe('slow');

    await user.click(screen.getByTestId('mock-mode-error'));
    expect(window.localStorage.getItem('eatwhat:location:mock_mode:v1')).toBe('error');
  });
});
