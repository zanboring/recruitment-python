import { defineStore } from 'pinia';

/** 后端 SSE `event: sources` 透出的知识库引用来源。 */
export interface ChatSource {
  index: number;
  id: number;
  question: string;
  answer?: string;
  source?: string;
  quality_score?: number;
}

/** 后端 SSE `event: meta` 透出的「本轮用了谁、花了多少」。 */
export interface ChatMeta {
  provider?: string;
  model?: string;
  tier?: 'cloud' | 'local' | 'fallback' | string;
  prompt_tokens?: number;
  completion_tokens?: number;
  latency_ms?: number;
  tool_called?: boolean;
}

export interface Message {
  role: 'user' | 'ai';
  content: string;
  timestamp: string;
  /** 回答依据的知识库条目（可选；仅 AI 消息、且后端有命中时存在） */
  sources?: ChatSource[];
  /** 本轮模型元信息（可选） */
  meta?: ChatMeta;
}

// 默认欢迎消息
const WELCOME_MESSAGE: Message = {
  role: 'ai',
  content: '你好！我是招聘系统的AI助手，有什么可以帮助你的吗？',
  timestamp: new Date().toLocaleTimeString()
};

interface ChatState {
  messages: Message[];
  loading: boolean;
  error: string;
  lastMessage: string;
}

export const useChatStore = defineStore('chat', {
  state: (): ChatState => ({
    messages: [WELCOME_MESSAGE],
    loading: false,
    error: '',
    lastMessage: ''
  }),

  getters: {
    isEmpty: (state) => state.messages.length <= 1,
    messageCount: (state) => state.messages.length
  },

  actions: {
    /** 添加用户消息 */
    addUserMessage(content: string) {
      const msg: Message = {
        role: 'user',
        content,
        timestamp: new Date().toLocaleTimeString()
      };
      this.messages.push(msg);
      this.lastMessage = content;
    },

    /** 添加 AI 空白消息（流式填充用） */
    createAiPlaceholder(): number {
      const msg: Message = {
        role: 'ai',
        content: '',
        timestamp: new Date().toLocaleTimeString()
      };
      this.messages.push(msg);
      return this.messages.length - 1; // 返回索引位置
    },

    /** 追加 AI 流式内容 */
    appendAiContent(index: number, chunk: string) {
      if (index >= 0 && index < this.messages.length) {
        this.messages[index].content += chunk;
      }
    },

    /** 设置 AI 完整内容（流式更新用） */
    setAiContent(index: number, content: string) {
      if (index >= 0 && index < this.messages.length) {
        this.messages[index].content = content;
      }
    },

    /** 记录某条 AI 消息的引用来源与模型元信息（来自 SSE 具名事件） */
    setAiExtras(index: number, extras: { sources?: ChatSource[]; meta?: ChatMeta }) {
      if (index < 0 || index >= this.messages.length) return;
      const msg = this.messages[index];
      if (extras.sources && extras.sources.length > 0) {
        msg.sources = extras.sources;
      }
      if (extras.meta && Object.keys(extras.meta).length > 0) {
        msg.meta = extras.meta;
      }
    },

    /** 设置 AI 最终错误内容 */
    setAiError(index: number, errorContent: string) {
      if (index >= 0 && index < this.messages.length) {
        if (this.messages[index].content === '') {
          this.messages[index].content = errorContent;
        }
      }
    },

    /** 设置加载状态 */
    setLoading(loading: boolean) {
      this.loading = loading;
    },

    /** 设置错误信息 */
    setError(error: string) {
      this.error = error;
    },

    /** 清空所有聊天记录（保留欢迎消息） */
    clearMessages() {
      this.messages = [{ ...WELCOME_MESSAGE, timestamp: new Date().toLocaleTimeString() }];
      this.error = '';
      this.lastMessage = '';
    }
  }
});
