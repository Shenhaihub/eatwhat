/**
 * P3-02/P3-03: POI 商户搜索接口类型（1:1 映射后端 app/schemas/poi.py）。
 *
 * G-16 契约：
 * - API 响应不含精确坐标（lat/lng），只有 distance_m（粗略距离）。
 * - location_token 是不透明字符串，前端不解析其内容。
 * - meta.provider_mode = "mock" / "live" 必须返回，前端用于来源标注。
 *
 * 设计要点（14_设计审计 §6.3）：
 * - 商户结果称"最近匹配"，禁止"最好吃/最推荐"。
 * - 未知营业状态显示未知，不推断。
 */

/** POIProviderName enum：数据来源。 */
export type POIProviderName = 'mock' | 'amap';

/** MockMode：测试专用四态（仅在 POI_PROVIDER=mock 时生效）。 */
export type MockMode = 'normal' | 'empty' | 'slow' | 'error';

/**
 * POIItem - 单条商户结果（1:1 映射后端 POIItem）。
 * G-16：不含 lat/lng 精确坐标；distance_m 是粗略距离（整数米）。
 */
export interface POIItem {
  readonly provider: POIProviderName;
  readonly poi_id: string;
  readonly name: string;
  readonly category_text: string;
  readonly distance_m: number;
  readonly address: string;
  readonly city_name: string;
  readonly district_name: string;
  readonly map_uri: string;
}

/** 1:1 映射后端 RestaurantSearchMeta。 */
export interface RestaurantSearchMeta {
  readonly next_cursor: string | null;
  readonly cached: boolean;
  readonly provider_mode: 'mock' | 'live';
  readonly request_id: string;
}

/** 1:1 映射后端 RestaurantSearchSuggestion。 */
export interface RestaurantSearchSuggestion {
  readonly action: 'expand_radius' | 'select_other_food';
  readonly radius_m: number | null;
}

/** 1:1 映射后端 RestaurantSearchRequestV1。 */
export interface RestaurantSearchRequestV1 {
  readonly food_code: string;
  readonly location_token: string;
  readonly radius_m?: number;
  readonly limit?: number;
  readonly cursor?: string | null;
  readonly recommendation_id?: string | null;
  readonly mock_mode?: MockMode | null;
}

/** 1:1 映射后端 RestaurantSearchResponseV1。 */
export interface RestaurantSearchResponseV1 {
  readonly data: readonly POIItem[];
  readonly meta: RestaurantSearchMeta;
  readonly suggestions: readonly RestaurantSearchSuggestion[];
}
