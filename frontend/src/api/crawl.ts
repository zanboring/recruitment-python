import http from './http';

export interface CrawlTaskPayload {
  sourceSite?: string;
  keyword?: string;
  city?: string;
}

export interface CrawlTask {
  id: number;
  sourceSite: string;
  keyword: string;
  city: string;
  status: 'PENDING' | 'RUNNING' | 'FINISHED' | 'FAILED';
  jobCount: number;
  message: string;
  createdAt: string;
  finishedAt?: string;
}

/**
 * 平台选项。`implemented` 表示后端是否真的有该平台的采集实现 ——
 * 未实现的平台仍会列出（便于说明「有这个规划」），但必须在界面上置灰，
 * 否则用户选中的是一个**注定失败**的任务。
 */
export interface CrawlPlatformOption {
  value: string;
  label: string;
  implemented: boolean;
}

export interface CrawlOptions {
  platforms: CrawlPlatformOption[];
  cities: string[];
}

/**
 * 拉取爬取可选项（平台与城市）。
 *
 * 此前这两份清单硬编码在对话框组件里：平台写了 4 个而后端只实现 1 个，城市写了
 * 11 个而后端收录 20 个。支持范围一变前端就错，且没有任何提示。改由后端提供后，
 * 支持范围变化时前端零改动。
 */
export function fetchCrawlOptions(): Promise<CrawlOptions> {
  return http.get('/crawl/options');
}

export function createCrawlTask(data: CrawlTaskPayload): Promise<number> {
  return http.post('/crawl/task', data);
}

export function startCrawlTask(id: number): Promise<void> {
  return http.post(`/crawl/task/${id}/start`);
}

export function fetchCrawlTasks(): Promise<CrawlTask[]> {
  return http.get('/crawl/tasks');
}

export function deleteCrawlTask(id: number): Promise<void> {
  return http.delete(`/crawl/task/${id}`);
}
