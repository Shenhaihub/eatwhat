/**P4-03 推荐历史接口类型 1:1。*/

export interface HistoryLocation {
  lng?: number;
  lat?: number;
  address?: string;
  city?: string;
  [k: string]: unknown;
}

export interface HistoryItemSnapshot {
  entry_intent?: string;
  questionnaire_version?: string;
  dictionary_version?: string;
  items: unknown[];
  [k: string]: unknown;
}

export interface HistoryRecord {
  id: string;
  user_id: string;
  food_code: string | null;
  location: HistoryLocation | null;
  radius_meters: number | null;
  tags: string[] | null;
  recommendation_snapshot: HistoryItemSnapshot;
  result_count: number;
  poi_provider: string | null;
  created_at: string;
  updated_at: string;
}

export interface HistoryListResponse {
  items: HistoryRecord[];
  total: number;
  limit: number;
  offset: number;
}

export interface HistoryWriteRequest {
  food_code?: string | null;
  location?: HistoryLocation | null;
  radius_meters?: number | null;
  tags?: string[] | null;
  recommendation_snapshot: HistoryItemSnapshot;
  result_count?: number;
  poi_provider?: string | null;
  created_at?: string;
}

export interface HistoryDeleteAllResponse {
  deleted: number;
}
