/**
 * 判断 job.url 是否为第三方招聘平台的「职位详情页」链接（可直达原文），
 * 排除列表搜索页、演示占位链接等。
 */
export function isPreciseJobSourceUrl(url: string | undefined | null): boolean {
  if (!url || typeof url !== 'string') return false;
  const u = url.trim();
  if (!u || u.includes('baidu.com')) return false;
  const l = u.toLowerCase();

  if (l.includes('zhipin.com')) {
    if (l.includes('/web/geek/jobs') || l.includes('/web/geek/job?')) return false;
    return l.includes('/job_detail/');
  }

  if (l.includes('sou.zhaopin.com')) return false;
  if (l.includes('jobs.zhaopin.com') && /\.htm(l)?(\?|$)/i.test(u)) return true;

  if (l.includes('51job.com') && l.includes('/jobs/')) return true;
  if (l.includes('liepin.com') && /\/job\/[\w-]+/.test(l)) return true;

  return false;
}
