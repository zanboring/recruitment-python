import http from './http';

export interface ReportSummary {
  id: number;
  report_date: string;
  title: string;
  ai_summary: string;
  generated_by: string;
  new_jobs: number;
  active_jobs: number;
  total_jobs: number;
  status: string;
  message: string | null;
  has_excel: boolean;
}

export interface CityPlatformPivot {
  /** 列维度：来源平台（按总量倒序） */
  platforms: string[];
  rows: { city: string; counts: Record<string, number>; total: number }[];
  totals: Record<string, number>;
  grand_total: number;
  /** 因超出 top_n 未列出的城市数（0 表示全部列出） */
  truncated_cities: number;
}

export interface PushConfig {
  /** 开关已打开且 URL 已配置，推送按钮可用 */
  configured: boolean;
  /** 是否打开了 report_webhook_enabled */
  enabled: boolean;
  type: string;
}

export interface ReportDetail extends ReportSummary {
  stats: {
    date: string;
    new_today: number;
    summary: { total: number; active: number; avg_salary: number };
    city: { name: string; count: number }[];
    skill: { name: string; count: number }[];
    salary_range: { name: string; count: number }[];
    education: { name: string; count: number }[];
    experience: { name: string; count: number }[];
    /** 来源平台分布（旧快照可能没有该字段） */
    platform?: { name: string; count: number }[];
    /** 城市 × 平台 透视表（旧快照可能没有该字段） */
    city_platform_pivot?: CityPlatformPivot;
  };
  /** 推送配置状态 */
  push?: PushConfig;
}

export interface PushResult {
  success: boolean;
  target: string;
  detail: string;
  message: string;
}

/** 手动生成当日日报（管理员） */
export function generateReportApi() {
  return http.post('/api/reports/generate');
}

/** 最新一期日报 */
export function getLatestReportApi(): Promise<ReportDetail> {
  return http.get('/api/reports/latest');
}

/** 日报列表 */
export function getReportListApi(limit = 10, offset = 0): Promise<ReportSummary[]> {
  return http.get('/api/reports/list', { params: { limit, offset } });
}

/** 下载日报 Excel */
export function downloadReportExcelApi(id: number): Promise<Blob> {
  return http.get(`/api/reports/${id}/download`, { responseType: 'blob' });
}

/** 手动推送某期日报到 Webhook（管理员） */
export function pushReportApi(id: number): Promise<PushResult> {
  return http.post(`/api/reports/${id}/push`);
}
