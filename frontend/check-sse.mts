// SSE 解析器验证脚本（node --experimental-strip-types check-sse.mts）
import { createSseParser, parseEventBlock, safeParseJson } from './src/api/sse.ts';

let pass = 0, fail = 0;
const check = (label: string, cond: boolean, detail = '') => {
  if (cond) { pass++; console.log(`  [OK  ] ${label} ${detail}`); }
  else { fail++; console.log(`  [FAIL] ${label} ${detail}`); }
};

console.log('=== 1. 单个事件块解析 ===');
check('默认事件名为 message', parseEventBlock('data: 你好')?.event === 'message');
check('data 前导空格被去掉', parseEventBlock('data: 你好')?.data === '你好');
check('无前导空格也正确', parseEventBlock('data:你好')?.data === '你好');
check('具名事件被识别', parseEventBlock('event: meta\ndata: {"a":1}')?.event === 'meta');
check('多行 data 用换行连接', parseEventBlock('data: 第一行\ndata: 第二行')?.data === '第一行\n第二行');
check('注释行被忽略', parseEventBlock(': keep-alive\ndata: x')?.data === 'x');
check('无 data 的事件返回 null', parseEventBlock('event: ping') === null);
check('CRLF 换行可解析', parseEventBlock('data: a\r\ndata: b')?.data === 'a\nb');

console.log('\n=== 2. 增量解析：事件被切分到多个 chunk ===');
{
  const push = createSseParser();
  const e1 = push('data: 你');
  const e2 = push('好\n\ndata: 世界\n\n');
  check('未闭合的分片不产出事件', e1.length === 0, `got ${e1.length}`);
  check('补齐后一次产出两个事件', e2.length === 2, `got ${e2.length}`);
  check('正文内容正确拼接', e2.map(e => e.data).join('') === '你好世界');
}

console.log('\n=== 3. 一次喂入多个事件 ===');
{
  const push = createSseParser();
  const events = push('data: A\n\ndata: B\n\ndata: C\n\n');
  check('三个事件全部产出', events.length === 3);
  check('顺序保持', events.map(e => e.data).join('') === 'ABC');
}

console.log('\n=== 4. 真实后端格式（正文 + 具名事件） ===');
{
  const push = createSseParser();
  const wire =
    'event: sources\ndata: [{"index":1,"id":7,"question":"薪资怎么算"}]\n\n' +
    'data: 根据\n\n' +
    'data: 资料\n\n' +
    'event: meta\ndata: {"provider":"deepseek","tier":"cloud"}\n\n';
  // 故意按不规则的边界切开，模拟 TCP 分包
  const events = [
    ...push(wire.slice(0, 20)),
    ...push(wire.slice(20, 55)),
    ...push(wire.slice(55)),
  ];
  const sources = events.find(e => e.event === 'sources');
  const meta = events.find(e => e.event === 'meta');
  const text = events.filter(e => e.event === 'message').map(e => e.data).join('');

  check('sources 事件被解析', !!sources);
  check('sources 来源顺序在最前', events[0]?.event === 'sources');
  check('正文只有纯文本（不含 data: 前缀）', text === '根据资料', JSON.stringify(text));
  check('meta 事件被解析', JSON.parse(meta?.data || '{}').tier === 'cloud');
}

console.log('\n=== 5. 容错 ===');
check('非法 JSON 返回 null 而不抛异常', safeParseJson('{bad') === null);
check('合法 JSON 正常解析', safeParseJson('{"a":1}')?.a === 1);
{
  const push = createSseParser();
  const events = push('data: [已取消]\n\n');
  check('取消标记作为正文处理', events[0].data === '[已取消]');
}

console.log(`\n=== 结果：${pass} 通过 / ${fail} 失败 ===`);
process.exit(fail === 0 ? 0 : 1);
