/**
 * P3-01: 地点上下文接口类型（1:1 映射后端 app/schemas/location.py）。
 *
 * G-16 契约：
 * - 精确坐标（WGS84→GCJ-02）只用于当前附近搜索，不写入 URL、普通日志、业务历史、公共分享。
 * - location_token 是不透明字符串，内部不含坐标，只映射到内存中的 LocationContext。
 * - API 响应只暴露 display_name/city_name/district_name，绝不暴露坐标。
 *
 * 端点：
 * - POST /api/v1/locations/search             手动地点搜索
 * - POST /api/v1/locations/reverse             浏览器定位反向地理编码（坐标在 body，不在 URL）
 * - GET  /api/v1/locations/demo                演示地点列表
 * - POST /api/v1/locations/demo/{code}/select  选择演示地点
 */

/** LocationSource enum：地点来源（名词表 §5）。 */
export type LocationSource = 'browser' | 'manual' | 'demo';

/**
 * LocationTokenInfo - API 响应中暴露的地点信息（G-16：不含坐标）。
 * 1:1 映射后端 LocationTokenInfo。
 */
export interface LocationTokenInfo {
  /** 短时不透明 token，用于后续商家搜索（内部不含坐标） */
  readonly location_token: string;
  readonly display_name: string;
  readonly city_name: string;
  readonly district_name: string;
}

// ---- POST /api/v1/locations/search ----

/** 1:1 映射后端 LocationSearchRequestV1。 */
export interface LocationSearchRequestV1 {
  /** 搜索关键词（1~64 字符） */
  readonly keyword: string;
  /** 可选城市限定（最长 32 字符） */
  readonly city?: string | null;
  /** 返回条数，1~10，默认 5 */
  readonly limit?: number;
}

/** 1:1 映射后端 LocationSearchResponseV1。 */
export interface LocationSearchResponseV1 {
  readonly data: readonly LocationTokenInfo[];
}

// ---- POST /api/v1/locations/reverse ----

/**
 * 1:1 映射后端 LocationReverseRequestV1。
 * G-16：坐标在 POST body 中，不在 URL；坐标系为 WGS84（浏览器原生）。
 */
export interface LocationReverseRequestV1 {
  /** WGS84 纬度，[-90.0, 90.0] */
  readonly latitude: number;
  /** WGS84 经度，[-180.0, 180.0] */
  readonly longitude: number;
}

/** 1:1 映射后端 LocationReverseResponseV1。 */
export interface LocationReverseResponseV1 {
  readonly data: LocationTokenInfo;
}

// ---- GET /api/v1/locations/demo ----

/** 1:1 映射后端 DemoLocationItem（不含坐标，不含 token）。 */
export interface DemoLocationItem {
  /** 演示地点 code，匹配 ^[a-z0-9_]{2,40}$ */
  readonly code: string;
  readonly display_name: string;
  readonly city_name: string;
  readonly district_name: string;
}

/** 1:1 映射后端 DemoLocationListResponse。 */
export interface DemoLocationListResponse {
  readonly data: readonly DemoLocationItem[];
}

// ---- POST /api/v1/locations/demo/{code}/select ----

/** 1:1 映射后端 DemoLocationSelectResponse。 */
export interface DemoLocationSelectResponse {
  readonly data: LocationTokenInfo;
}
