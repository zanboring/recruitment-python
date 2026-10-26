import http from './http';

export interface JobQuery {
  keyword?: string;
  city?: string;
  companyName?: string;
  experience?: string;
  education?: string;
  status?: string;
  sourceSite?: string;
  minSalary?: number;
  maxSalary?: number;
  pageNum?: number;
  pageSize?: number;
}

export interface JobItem {
  id: number;
  companyId?: number;
  title: string;
  companyName: string;
  sourceSite: string;
  jobKey: string;
  jobStatus: 'NEW' | 'ACTIVE' | 'OFFLINE';
  city: string;
  experience: string;
  education: string;
  minSalary?: number;
  maxSalary?: number;
  salaryUnit: string;
  skills: string;
  jobDesc?: string;
  url?: string;
  publishTime?: string;
  lastSeenAt?: string;
  createdAt: string;
}

export interface JobStatItem {
  name: string;
  count: number;
  avgSalary?: number;
}

export interface PageResult<T> {
  list: T[];
  total: number;
  pageNum: number;
  pageSize: number;
  pages: number;
}

export function fetchJobPage(data: JobQuery): Promise<PageResult<JobItem>> {
  return http.post('/jobs/page', data);
}

export function fetchJobStatCity(): Promise<JobStatItem[]> {
  return http.get('/jobs/stat/city');
}

export function fetchJobStatCompany(): Promise<JobStatItem[]> {
  return http.get('/jobs/stat/company');
}

export function fetchJobStatSkill(): Promise<JobStatItem[]> {
  return http.get('/jobs/stat/skill');
}

export function fetchJobStatSalaryRange(): Promise<JobStatItem[]> {
  return http.get('/jobs/stat/salary-range');
}

export function fetchJobStatEducation(): Promise<JobStatItem[]> {
  return http.get('/jobs/stat/education');
}

export function fetchJobStatExperience(): Promise<JobStatItem[]> {
  return http.get('/jobs/stat/experience');
}

export function fetchJobStatStatus(): Promise<JobStatItem[]> {
  return http.get('/jobs/stat/status');
}

export function predictSalary(params: {
  city?: string;
  experience?: string;
  education?: string;
  skills?: string;
}): Promise<number> {
  return http.get('/jobs/predict-salary', { params });
}

export function recommendJobs(params: { skills?: string; education?: string; experience?: number; city?: string }): Promise<JobItem[]> {
  return http.get('/jobs/recommend', { params });
}

export function fetchAnalysisSummary(): Promise<string> {
  return http.get('/jobs/analysis/summary');
}

export function fetchTopTitles(): Promise<JobStatItem[]> {
  return http.get('/jobs/analysis/top-titles');
}

export interface AIAnalysisResult {
  summary: string;
  qualityJobs: QualityJob[];
  trendAnalysis: TrendAnalysis;
  skillDemands: SkillDemand[];
  salaryAnalysis: SalaryAnalysis;
  suggestions: string[];
}

export interface QualityJob {
  id: number;
  title: string;
  companyName: string;
  city: string;
  salary: string;
  skills: string;
  recommendReason: string;
}

export interface TrendAnalysis {
  trendText: string;
  hotCity: string;
  hotSkill: string;
  hotTitle: string;
}

export interface SkillDemand {
  skill: string;
  count: number;
  level: '非常热门' | '热门' | '一般';
}

export interface SalaryAnalysis {
  avgSalary: string;
  topSalary: string;
  salaryRange: string;
}

export function aiAnalysis(data?: JobQuery): Promise<AIAnalysisResult> {
  return http.post('/jobs/ai-analysis', data || {});
}

/* ---------- 技能画像 ---------- */
export interface SkillProfileJob {
  job_id: number; title: string; company_name: string; city: string;
  min_salary?: number; max_salary?: number; education?: string; experience?: string;
  skills: string; score: number; coverage: number;
  matched: string[]; missing_top: string[];
}
export interface SkillCountItem { skill: string; count: number }
export interface SkillProfileResult {
  my_skills: string[];
  summary: { analyzed: number; covered_ratio: number };
  jobs: SkillProfileJob[];
  missing_skills: SkillCountItem[];
  top_demanded: SkillCountItem[];
}
export function skillProfile(data: { skills: string; city?: string; limit?: number }): Promise<SkillProfileResult> {
  return http.post('/jobs/skill-profile', data);
}

/** 方案A：获取岗位本地详情页HTML */
export interface JobDetailHtmlResult {
  id: number;
  detailHtml: string;
}

export function getJobDetailHtml(jobId: number): Promise<JobDetailHtmlResult> {
  return http.get(`/jobs/${jobId}/detail-html`);
}

