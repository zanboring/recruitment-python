import asyncio
import logging
from typing import List, Dict

from bs4 import BeautifulSoup

from app.crawlers.base import BaseCrawler
from app.crawlers.city_map import require_city_code
from app.crawlers.cleaner import clean_job_data, generate_job_key
from app.config import settings

logger = logging.getLogger("boss_crawler")


SELECTORS = {
    "job_card": [
        "div.job-card",
        "div[class*='job-card']",
        "div[class*='JobCard']",
        "div.job-card-wrapper",
        "div[class*='card']",
    ],
    "job_name": [
        "span.job-name",
        "span[class*='job-name']",
        "span[class*='JobName']",
        "h3[class*='job']",
        "div[class*='title']",
    ],
    "salary": [
        "span.salary",
        "span[class*='salary']",
        "span[class*='Salary']",
        "div[class*='salary']",
    ],
    "company_name": [
        "span.company-name",
        "span[class*='company-name']",
        "span[class*='CompanyName']",
        "div[class*='company']",
        "span[class*='name']",
    ],
    "job_area": [
        "span.job-area",
        "span[class*='job-area']",
        "span[class*='JobArea']",
        "span[class*='area']",
        "span[class*='location']",
    ],
    "tags": [
        "div.tags",
        "div[class*='tags']",
        "div[class*='Tags']",
        "div[class*='tag']",
    ],
    "tag": [
        "span.tag",
        "span[class*='tag']",
        "span[class*='Tag']",
        "em",
    ],
}


def find_element(soup, selectors):
    for selector in selectors:
        if selector.startswith("div[class*="):
            class_pattern = selector.split("*=")[1].rstrip("]").strip("'\"")
            elements = soup.find_all("div", class_=lambda c: c and class_pattern.lower() in c.lower())
            if elements:
                return elements[0]
        elif selector.startswith("span[class*="):
            class_pattern = selector.split("*=")[1].rstrip("]").strip("'\"")
            elements = soup.find_all("span", class_=lambda c: c and class_pattern.lower() in c.lower())
            if elements:
                return elements[0]
        elif selector.startswith("h3[class*="):
            class_pattern = selector.split("*=")[1].rstrip("]").strip("'\"")
            elements = soup.find_all("h3", class_=lambda c: c and class_pattern.lower() in c.lower())
            if elements:
                return elements[0]
        else:
            parts = selector.split(".")
            tag = parts[0] if len(parts) > 1 else selector
            class_name = parts[1] if len(parts) > 1 else None
            if class_name:
                elem = soup.find(tag, class_=class_name)
            else:
                elem = soup.find(tag)
            if elem:
                return elem
    return None


def find_all_elements(soup, selectors):
    for selector in selectors:
        if selector.startswith("div[class*="):
            class_pattern = selector.split("*=")[1].rstrip("]").strip("'\"")
            elements = soup.find_all("div", class_=lambda c: c and class_pattern.lower() in c.lower())
            if elements:
                return elements
        elif selector.startswith("span[class*="):
            class_pattern = selector.split("*=")[1].rstrip("]").strip("'\"")
            elements = soup.find_all("span", class_=lambda c: c and class_pattern.lower() in c.lower())
            if elements:
                return elements
        else:
            parts = selector.split(".")
            tag = parts[0] if len(parts) > 1 else selector
            class_name = parts[1] if len(parts) > 1 else None
            if class_name:
                elements = soup.find_all(tag, class_=class_name)
            else:
                elements = soup.find_all(tag)
            if elements:
                return elements
    return []


class BossCrawler(BaseCrawler):
    def __init__(self):
        super().__init__("boss")

    async def _wait_for_any_selector(self, page, selectors, timeout=10000):
        for selector in selectors:
            try:
                await page.wait_for_selector(selector, timeout=timeout)
                return selector
            except Exception:
                continue
        raise TimeoutError(f"None of the selectors found: {selectors}")

    async def crawl(self, keyword: str, city: str = "") -> List[Dict]:
        results = []
        # 未收录城市直接抛错。此前用「查不到就回退北京编码」，会把北京的岗位
        # 当成目标城市的数据入库，且不报错、不留痕（详见 city_map 模块文档）。
        # 在这里抛出还能避免无意义的 4 次指数退避重试 —— 这类失败重试不会变好。
        city_code = require_city_code(city)
        page_num = 1

        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    user_agent=self.get_random_ua(),
                    viewport={"width": 1920, "height": 1080}
                )
                page = await context.new_page()

                while len(results) < settings.crawl_max_jobs:
                    url = f"https://www.zhipin.com/web/geek/job?query={keyword}&city={city_code}&page={page_num}"
                    try:
                        await page.goto(url, timeout=settings.crawl_timeout * 1000)
                        found_selector = await self._wait_for_any_selector(page, SELECTORS["job_card"], timeout=10000)
                        logger.info(f"Using selector: {found_selector}")
                        await asyncio.sleep(2)
                        html = await page.content()
                        page_results = await self.parse_page(html, keyword)
                        if not page_results:
                            break
                        results.extend(page_results)
                        await self.random_delay()
                        page_num += 1
                    except TimeoutError as e:
                        logger.warning(f"Page {page_num} timed out, selectors may have changed: {e}")
                        break
                    except Exception as e:
                        logger.error(f"Crawl failed for {keyword} in {city} page {page_num}: {e}", exc_info=True)
                        break

                await browser.close()
        except ImportError:
            logger.error("Playwright is not installed. Please install with: pip install playwright && playwright install chromium")
            raise

        cleaned = [clean_job_data(job) for job in results]
        return cleaned[:settings.crawl_max_jobs]

    async def parse_page(self, page_content: str, keyword: str) -> List[Dict]:
        jobs = []
        soup = BeautifulSoup(page_content, "html.parser")

        job_cards = find_all_elements(soup, SELECTORS["job_card"])
        if not job_cards:
            logger.warning("No job cards found, CSS selectors may need update")

        for card in job_cards:
            try:
                title_elem = find_element(card, SELECTORS["job_name"])
                salary_elem = find_element(card, SELECTORS["salary"])
                company_elem = find_element(card, SELECTORS["company_name"])
                location_elem = find_element(card, SELECTORS["job_area"])
                tags_elem = find_element(card, SELECTORS["tags"])

                title = title_elem.get_text(strip=True) if title_elem else ""
                salary = salary_elem.get_text(strip=True) if salary_elem else ""
                company_name = company_elem.get_text(strip=True) if company_elem else ""
                city = location_elem.get_text(strip=True) if location_elem else ""

                experience = ""
                education = ""
                skills = []

                if tags_elem:
                    tags = find_all_elements(tags_elem, SELECTORS["tag"])
                    for tag in tags:
                        tag_text = tag.get_text(strip=True)
                        if any(exp in tag_text for exp in ["年", "应届"]):
                            experience = tag_text
                        elif any(edu in tag_text for edu in ["大专", "本科", "硕士", "博士", "高中"]):
                            education = tag_text
                        else:
                            skills.append(tag_text)

                jobs.append({
                    "title": title,
                    "salary": salary,
                    "company_name": company_name,
                    "city": city,
                    "experience": experience,
                    "education": education,
                    "skills": ",".join(skills),
                    "source_site": self.source_site,
                    # job_key 统一走「平台 + 标题 + 公司 + 城市」四要素指纹。
                    # 原先用 BOSS 的 jobId，与管理端新增岗位的口径不同，
                    # 同一岗位经两个入口会算出不同的键而重复入库。
                    "job_key": generate_job_key(self.source_site, title, company_name, city),
                    "description": title + " " + ",".join(skills),
                })
            except Exception as e:
                logger.debug(f"Parse error for job card: {e}")
                continue

        return jobs