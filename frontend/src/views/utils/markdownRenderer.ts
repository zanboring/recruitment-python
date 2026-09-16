// @ts-nocheck
/**
 * Markdown 渲染器配置
 *
 * 为 AIChat 组件提供美化的 Markdown 渲染支持
 * 包含：标题、段落、列表、代码块、表格、引用块等自定义渲染
 * 所有颜色值均使用 CSS 变量（通过 getComputedStyle 获取）
 */
import { marked, Renderer } from 'marked';

/**
 * 从 CSS 变量获取实际颜色值
 * 由于渲染器运行时需要具体色值（内联 style），这里从 document 读取 CSS 变量
 */
function cssVar(name: string, fallback: string): string {
  if (typeof document !== 'undefined' && document.documentElement) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
  }
  return fallback;
}

/** 创建并返回配置好的 marked 渲染器实例 */
export function createMarkdownRenderer(): Renderer {
  const renderer = new Renderer();

  // 标题渲染 - 带 emoji + 渐变下划线
  renderer.heading = function(tokens, depth) {
    const text = this.parser.parseInline(tokens);
    const emojis = ['\uD83D\uDCA1', '\uD83D\uDCCA', '\uD83C\uDFAF', '\uD83D\uDCB0', '\uD83D\uDD25', '\u2B50', '\u2705'];
    const emoji = emojis[Math.min(depth - 1, emojis.length - 1)] || '\uD83D\uDCCC';
    const borderClr = cssVar('--md-heading-border', '#e8f0fe');
    const txtClr = cssVar('--md-heading-text', '#1a1a2e');
    return `<h${depth} style="margin-top:16px;margin-bottom:8px;padding-bottom:6px;border-bottom:2px solid ${borderClr};color:${txtClr};font-weight:700;">${emoji} ${text}</h${depth}>`;
  };

  // 段落渲染
  renderer.paragraph = function(tokens) {
    const text = this.parser.parseInline(tokens);
    const txtClr = cssVar('--md-body-text', '#3d3d3d');
    return `<p style="margin:10px 0;line-height:1.7;color:${txtClr};">${text}</p>`;
  };

  // 列表渲染
  renderer.list = function(body, ordered) {
    const tag = ordered ? 'ol' : 'ul';
    return `<${tag} style="padding-left:20px;margin:8px 0;list-style-position:outside;">${body}</${tag}>`;
  };

  // 代码块渲染（暗色主题风格）
  renderer.code = function(code, infostring) {
    const language = infostring || '';
    const bgCode = cssVar('--bg-code', '#f5f7fa');
    const primary = cssVar('--primary-color', '#667eea');
    const codeAccent = cssVar('--md-code-accent', '#e83e8c');
    return `<pre style="background:${bgCode};border-radius:8px;padding:12px;overflow-x:auto;margin:10px 0;border-left:4px solid ${primary};"><code class="language-${language}" style="font-family:'Fira Code',Consolas,Monaco,monospace;font-size:13px;color:${codeAccent};background:transparent;">${code}</code></pre>`;
  };

  // 表格渲染（渐变表头 + 斑马纹行）
  renderer.table = function(header, body) {
    const primary = cssVar('--primary-color', '#667eea');
    const primaryLight = cssVar('--primary-light', '#764ba2');
    const bgWhite = cssVar('--bg-white', '#fff');
    const bgAlt = cssVar('--bg-table-alt', '#f8f9fc');
    const tableTxt = cssVar('--md-table-text', '#555');
    const tblBorder = cssVar('--md-table-border', '#eee');
    
    let html = '<div style="overflow-x:auto;margin:10px 0;"><table style="width:100%;border-collapse:collapse;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.06);">';
    if (header) {
      html += `<thead><tr style="background:linear-gradient(135deg, ${primary}, ${primaryLight});color:#fff;">${header}</tr></thead>`;
    }
    html += `<tbody>${body}</tbody></table></div>`;
    return html;
  };

  // 引用块渲染（左侧蓝紫色渐变条）
  renderer.blockquote = function(quote) {
    const primary = cssVar('--primary-color', '#667eea');
    const bgBq = cssVar('--bg-blockquote', '#f8f9ff');
    const bqTxt = cssVar('--md-blockquote-text', '#556680');
    return `<blockquote style="border-left:4px solid ${primary};padding:10px 16px;margin:10px 0;background:${bgBq};border-radius:0 8px 8px 0;color:${bqTxt};font-style:italic;">${quote}</blockquote>`;
  };

  // 粗体渲染（暖色高亮背景）
  renderer.strong = function(text) {
    const strongClr = cssVar('--md-strong-color', '#e65100');
    const bgWarm = cssVar('--bg-highlight-warm', '#fff7ed');
    const bgYel = cssVar('--bg-highlight-yellow', '#fffbe6');
    return `<strong style="color:${strongClr};font-weight:700;background:linear-gradient(135deg, ${bgWarm}, ${bgYel});padding:1px 4px;border-radius:3px;">${text}</strong>`;
  };

  // 图片渲染（带XSS防护）
  renderer.image = function(href, title, text) {
    const cleanHref = sanitizeUrl(href);
    const cleanTitle = escapeHtml(title || '');
    const cleanText = escapeHtml(text || '');
    if (!cleanHref) return '';
    return `<img src="${cleanHref}" alt="${cleanText}" title="${cleanTitle}" style="max-width:100%;border-radius:8px;margin:10px 0;box-shadow:0 2px 8px rgba(0,0,0,0.1);">`;
  };

  // 链接渲染（带XSS防护）
  renderer.link = function(href, title, text) {
    const cleanHref = sanitizeUrl(href);
    const cleanTitle = escapeHtml(title || '');
    const cleanText = escapeHtml(text || '');
    if (!cleanHref) return cleanText;
    return `<a href="${cleanHref}" title="${cleanTitle}" target="_blank" rel="noopener noreferrer" style="color:#667eea;text-decoration:none;border-bottom:1px solid #667eea;">${cleanText}</a>`;
  };

  return renderer;
}

/**
 * URL安全过滤 - 防止XSS攻击
 */
function sanitizeUrl(url: string): string {
  if (!url || typeof url !== 'string') return '';
  const trimmed = url.trim();
  if (trimmed.toLowerCase().startsWith('javascript:') ||
      trimmed.toLowerCase().startsWith('data:') ||
      trimmed.toLowerCase().startsWith('vbscript:')) {
    return '';
  }
  return trimmed;
}

/**
 * HTML实体转义
 */
function escapeHtml(text: string): string {
  if (!text) return '';
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

/** 预创建的渲染器单例 */
export const mdRendererInstance: Renderer = createMarkdownRenderer();

/**
 * 将 Markdown 文本渲染为 HTML 字符串
 */
export function renderMarkdown(content: string): string {
  if (!content || !content.trim()) return '';
  try {
    let cleanedContent = content;
    
    // ====== 第一步：SSE格式清理 ======
    cleanedContent = cleanedContent
      .replace(/^data:\s*/gm, '')
      .replace(/^event:\w+\s*$/gm, '')
      .replace(/^event:\w+$/gm, '');
    
    // 修复被 SSE 切割的 emoji（如 "⚠event:message" -> "⚠️"）
    cleanedContent = cleanedContent.replace(/([\u2600-\u27BF])event:message/gi, '$1');
    
    // ====== 第二步：移除所有 Markdown 标记 ======
    // 移除所有 # 标题标记
    cleanedContent = cleanedContent
      .replace(/^#{1,6}\s*/gm, '')
      .replace(/^#{1,6}$/gm, '');
    // 移除代码块标记
    cleanedContent = cleanedContent
      .replace(/```[\s\S]*?```/g, '')
      .replace(/```\w*/g, '');
    // 移除所有反引号
    cleanedContent = cleanedContent.replace(/`{1,3}/g, '');
    // 移除水平分隔线
    cleanedContent = cleanedContent.replace(/^\s*[-*_]{3,}\s*$/gm, '');
    // 移除表格分隔线
    cleanedContent = cleanedContent.replace(/\|?\s*[:\-]+\s*[:\|\-\s]*\|/g, '');
    // 移除引用标记
    cleanedContent = cleanedContent.replace(/^\s*>\s*/gm, '');
    // 移除粗体、斜体、删除线标记
    cleanedContent = cleanedContent
      .replace(/\*{1,2}([^*]+)\*{1,2}/g, '$1')
      .replace(/_{1,2}([^_]+)_{1,2}/g, '$1')
      .replace(/~~([^~]+)~~/g, '$1')
      .replace(/~~/g, '');
    
    // ====== 第三步：移除行首的列表符号 ======
    cleanedContent = cleanedContent.replace(/^[\-\*\•·]\s*/gm, '');
    cleanedContent = cleanedContent.replace(/^\s*\d+\.\s+/gm, '');
    
    // ====== 第四步：修复常见的碎片化问题 ======
    
    // 修复 emoji 后面被切割的情况
    cleanedContent = cleanedContent.replace(/([\u2600-\u27BF])\s+(?=[^\s\u4E00-\u9FFF])/g, '$1');
    cleanedContent = cleanedContent.replace(/\s+([\u2600-\u27BF])/g, '$1');
    
    // 修复百分数：移除百分号前的多余空格
    cleanedContent = cleanedContent.replace(/(\d+\.?\d*)\s*%\s*/g, '$1%');
    
    // 修复编号格式（1 . 初 级 -> 1. 初级）
    cleanedContent = cleanedContent.replace(/(\d)\s+\.\s+([\u4E00-\u9FFFa-zA-Z])/g, '$1. $2');
    
    // ====== 第五步：清理标点和多余空格 ======
    // 清理重复标点
    cleanedContent = cleanedContent
      .replace(/([.!?，。！？]){2,}/g, '$1')
      .replace(/([,，]){2,}/g, '$1')
      .replace(/([:：]){2,}/g, '$1')
      .replace(/([;；]){2,}/g, '$1');
    // 移除连续的特殊字符
    cleanedContent = cleanedContent.replace(/[*_#|\\]{3,}/g, ' ');
    // 合并多个空格
    cleanedContent = cleanedContent.replace(/\s{2,}/g, ' ');
    // 移除行首行尾空格
    cleanedContent = cleanedContent.replace(/^[ \t]+|[ \t]+$/gm, '');
    
    // ====== 第六步：格式化美观显示 ======
    
    // 格式化标题【标题名】
    cleanedContent = cleanedContent.replace(
      /^【([^】]+)】/gm, 
      '<div style="background:linear-gradient(135deg, #667eea20, #764ba220);border-left:4px solid #667eea;padding:10px 16px;margin:16px 0 8px 0;border-radius:0 8px 8px 0;"><b style="color:#667eea;font-size:16px;">【$1】</b></div>'
    );
    
    // 格式化重点『关键词』
    cleanedContent = cleanedContent.replace(
      /『([^』]+)』/g, 
      '<b style="color:#e65100;background:linear-gradient(135deg, #fff7ed, #fffbe6);padding:2px 6px;border-radius:4px;">『$1』</b>'
    );
    
    // 格式化薪资数字
    cleanedContent = cleanedContent.replace(
      /(\d+[kK](?:-?\d+[kK])?)/g,
      '<b style="color:#d63384;background:#fce4ec;padding:1px 4px;border-radius:3px;font-weight:600;">$1</b>'
    );
    
    // 格式化百分数
    cleanedContent = cleanedContent.replace(
      /(\d+\.?\d*%)/g,
      '<b style="color:#007bff;">$1</b>'
    );
    
    // ====== 第七步：按换行转为段落 ======
    
    cleanedContent = cleanedContent
      .split('\n')
      .map(line => line.trim())
      .filter(line => line.length > 0)
      .map(line => {
        if (line.startsWith('<') && line.endsWith('>')) {
          return line;
        }
        return `<p style="margin:10px 0;line-height:1.8;font-size:14px;color:#333;">${line}</p>`;
      })
      .join('');
    
    return cleanedContent;
  } catch {
    const div = document.createElement('div');
    div.textContent = content;
    return div.innerHTML;
  }
}