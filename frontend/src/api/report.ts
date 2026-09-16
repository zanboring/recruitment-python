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
  };
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
