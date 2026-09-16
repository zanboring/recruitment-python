import { marked, Marked } from 'marked';

// 检查 marked 18 的 API
console.log('=== marked top-level keys ===');
const keys = Object.keys(marked).filter(k => !k.startsWith('_'));
console.log(keys);

// 检查 Marked 类
console.log('\n=== Marked static keys ===');
const mkKeys = Object.getOwnPropertyNames(Marked).filter(k => k !== 'length' && k !== 'name' && k !== 'prototype');
console.log(mkKeys);

// 检查实例方法
const instance = new Marked();
const proto = Object.getOwnPropertyNames(Object.getPrototypeOf(instance)).filter(k => k !== 'constructor');
console.log('\n=== Marked instance methods ===');
console.log(proto);

// 检查 extensions
console.log('\n=== marked extensions ===');
if (marked.extensions) {
  console.log('extensions:', Object.keys(marked.extensions));
} else {
  console.log('no extensions property on marked');
}

// 尝试 parse
try {
  const result = marked.parse('# Hello **world**');
  console.log('\n=== parse test OK ===');
  console.log(result);
} catch(e) {
  console.log('\n=== parse error:', e.message);
}

// 检查 gfm 选项
try {
  const r = marked.parse('- [ ] task', { breaks: true });
  console.log('\n=== GFM test (task list) ===');
  console.log(r);
} catch(e) {
  console.log('GFM error:', e.message);
}
