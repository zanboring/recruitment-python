/**
 * SSE（Server-Sent Events）解析工具。
 *
 * **背景**：原先 AIChat.vue 直接把响应字节拼接进消息内容：
 *
 *     const chunk = decoder.decode(value, { stream: true });
 *     fullContent += chunk;
 *
 * 后端发的是标准 SSE（`data: 正文\n\n`），于是聊天框里会连 `data: ` 前缀和
 * 空行一起显示出来。接口是通的、页面也不报错，只是**内容里混着协议格式** ——
 * 这类问题不查网络面板很难发现。
 *
 * 这里按 SSE 规范实现增量解析，处理三个容易踩的点：
 *
 * 1. **事件可能被切分到多个 chunk**：TCP 不保证「一次 read = 一个完整事件」，
 *    必须缓冲未闭合的部分，等下一个 chunk 补齐再解析。
 * 2. **行分隔符有三种**：`\r\n` / `\n` / `\r`，规范都允许。
 * 3. **`data:` 后允许一个前导空格**，属于协议格式而非内容，必须去掉 ——
 *    否则正文每个分片都会多一个空格。
 *
 * 另外：具名事件（`event: sources`）与默认事件（无 event 字段，事件名为
 * `message`）都要支持，前者用于来源与元信息，后者是正文。
 */

export interface SseEvent {
  /** 事件名；未显式声明时为规范默认值 'message' */
  event: string;
  /** 该事件的 data 内容（多行 data 已用 \n 连接，前导空格已去掉） */
  data: string;
}

/** 空白行即事件边界；三种换行组合都要认。 */
const EVENT_BOUNDARY = /\r\n\r\n|\n\n|\r\r/;

/** 单条事件内部的行分隔。 */
const LINE_BREAK = /\r\n|\n|\r/;

/**
 * 解析一个完整的事件块，解析不出内容时返回 null。
 *
 * 规范细节：
 * - 以 `:` 开头的行是注释，忽略；
 * - 无冒号的行视作「字段名 + 空值」；
 * - 一个事件内的多条 `data` 用换行连接。
 */
export function parseEventBlock(block: string): SseEvent | null {
  let event = 'message';
  const dataLines: string[] = [];

  for (const line of block.split(LINE_BREAK)) {
    if (line === '' || line.startsWith(':')) continue;

    const colon = line.indexOf(':');
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? '' : line.slice(colon + 1);
    // 规范：冒号后若紧跟一个空格，该空格属于格式，不属于值
    if (value.startsWith(' ')) value = value.slice(1);

    if (field === 'event') event = value;
    else if (field === 'data') dataLines.push(value);
  }

  if (dataLines.length === 0) return null;
  return { event, data: dataLines.join('\n') };
}

/**
 * 创建一个增量解析器：每次喂入解码后的字符串，返回本次能完整解析出的事件。
 *
 * 用法：
 * ```ts
 * const push = createSseParser();
 * for (const evt of push(decoder.decode(value, { stream: true }))) { ... }
 * ```
 */
export function createSseParser() {
  let buffer = '';

  return function push(chunk: string): SseEvent[] {
    buffer += chunk;
    const events: SseEvent[] = [];

    for (;;) {
      const match = EVENT_BOUNDARY.exec(buffer);
      if (!match || match.index === undefined) break;

      const block = buffer.slice(0, match.index);
      buffer = buffer.slice(match.index + match[0].length);

      const parsed = parseEventBlock(block);
      if (parsed) events.push(parsed);
    }

    return events;
  };
}

/** 安全解析 JSON；失败返回 null（协议字段解析失败不该让对话崩掉）。 */
export function safeParseJson<T = unknown>(text: string): T | null {
  try {
    return JSON.parse(text) as T;
  } catch {
    return null;
  }
}
