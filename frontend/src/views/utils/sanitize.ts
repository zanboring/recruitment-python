/**
 * HTML 消毒（XSS 防线）—— 支持 `v-html` 的内容必须先过这里。
 *
 * ## 为什么把正则黑名单换掉
 *
 * 原实现是这两行：
 * ```js
 * .replace(/<script[^>]*>.*?<\/script>/gis, '')
 * .replace(/\s+on\w+\s*=\s*["'][^"']*["']/gi, '')
 * ```
 * 它只能挡住「教科书式」的 payload。实测（`node scripts/xss-check.mjs` 里的
 * 旧实现对照）两版正则的漏点是**不一样的**，加规则并不会让它变安全：
 *
 * | payload | ChatMessageList 版（3 条规则） | JobCard 版（2 条规则） |
 * |---|---|---|
 * | `<svg/onload=alert(1)>` | 挡住（无引号分支命中） | **漏**：只处理带引号的事件属性 |
 * | `<img src=x onerror=alert(1)>` | 挡住 | **漏** |
 * | `<iframe src="//evil">` | 挡住（显式删 iframe） | **漏**：根本没处理 iframe |
 * | `<a href="java&#9;script:...">` | **漏**：HTML 实体在浏览器解析阶段才解码，`java&#9;script:` 不命中 `javascript:` | **漏** |
 * | `<math><mtext><script>` 变形 | **漏**：按 `<script>` 整段删除，畸形嵌套可绕过 | **漏** |
 *
 * 根本原因不是「规则不够多」，而是 **HTML 不是正则语言** —— 补规则永远追不上。
 * 必须换成解析器 + 白名单（DOMPurify 做的就是这件事）。
 *
 * ## 为什么两套配置
 *
 * - {@link sanitizeChatHtml}：AI 回答。样式是 `markdownRenderer.ts` 用固定模板拼出来的
 *   （`<h1 style="...">`、`<table style="...">`），所以必须放行 `style`，
 *   否则整段回答会掉成无格式文本。可注入的只有正文内容，由 DOMPurify 统一处理。
 * - {@link sanitizeSnapshotHtml}：第三方页面快照。这部分的 style 完全不受我们控制，
 *   一个 `position:fixed;inset:0` 就能做全屏钓鱼遮罩，所以**一律剥掉**。
 *   排版退化由外层 CSS 兜底 —— 可用性让位于安全。
 */
import DOMPurify from 'dompurify';

/** AI 回答（markdown 渲染结果）允许的标签。 */
const CHAT_TAGS = [
  'p', 'br', 'hr', 'span', 'div', 'b', 'strong', 'i', 'em', 'u', 's', 'del',
  'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
  'ul', 'ol', 'li', 'blockquote', 'code', 'pre',
  'table', 'thead', 'tbody', 'tr', 'th', 'td',
  'a', 'img',
];

const CHAT_ATTR = ['style', 'class', 'href', 'title', 'target', 'rel', 'src', 'alt', 'colspan', 'rowspan'];

/** 第三方快照允许的标签：只要排版，不要任何可交互元素。 */
const SNAPSHOT_TAGS = [
  'p', 'br', 'hr', 'span', 'div', 'b', 'strong', 'i', 'em', 'u',
  'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
  'ul', 'ol', 'li', 'blockquote', 'code', 'pre',
  'table', 'thead', 'tbody', 'tr', 'th', 'td',
];

const SNAPSHOT_ATTR = ['href', 'title', 'colspan', 'rowspan'];

/**
 * 绝对禁止的标签。
 * `script` 默认就被 DOMPurify 拦，这里显式列出是为了「读代码的人不用猜」。
 */
const FORBID_TAGS = [
  'script', 'style', 'iframe', 'object', 'embed', 'form', 'input', 'button',
  'textarea', 'select', 'option', 'meta', 'link', 'base', 'svg', 'math', 'template',
];

/** 绝对禁止的属性：`srcset` 曾是绕过 `src` 限制的老路子。 */
const FORBID_ATTR = ['srcset', 'formaction', 'xlink:href', 'onerror', 'onload'];

/** 只允许这些协议（相对路径也放行）。`javascript:` / `data:text/html` 会被拦。 */
const SAFE_URI = /^(?:(?:https?|mailto|tel):|[^a-z]|[a-z+.-]+(?:[^a-z+.-:]|$))/i;

function hardenLinks(root: ParentNode): void {
  // 外链一律加 rel，避免 window.opener 被反向控制
  root.querySelectorAll('a[href]').forEach((a) => {
    a.setAttribute('target', '_blank');
    a.setAttribute('rel', 'noopener noreferrer');
  });
}

/** 消毒 AI 回答（保留渲染器生成的内联样式）。 */
export function sanitizeChatHtml(html: string): string {
  if (!html) return '';
  const clean = DOMPurify.sanitize(html, {
    ALLOWED_TAGS: CHAT_TAGS,
    ALLOWED_ATTR: CHAT_ATTR,
    FORBID_TAGS,
    FORBID_ATTR,
    ALLOW_DATA_ATTR: false,
    ALLOWED_URI_REGEXP: SAFE_URI,
    // 禁止解析失败时降级成原样返回
    RETURN_TRUSTED_TYPE: false,
  });
  const tpl = document.createElement('template');
  tpl.innerHTML = String(clean);
  hardenLinks(tpl.content);
  return tpl.innerHTML;
}

/**
 * 消毒第三方页面快照（最严格：无 style、无脚本、无可交互元素）。
 */
export function sanitizeSnapshotHtml(html: string): string {
  if (!html) return '';
  return String(
    DOMPurify.sanitize(html, {
      ALLOWED_TAGS: SNAPSHOT_TAGS,
      ALLOWED_ATTR: SNAPSHOT_ATTR,
      FORBID_TAGS,
      FORBID_ATTR,
      ALLOW_DATA_ATTR: false,
      ALLOWED_URI_REGEXP: SAFE_URI,
      RETURN_TRUSTED_TYPE: false,
    }),
  );
}
