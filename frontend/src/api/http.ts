import axios, { type AxiosInstance } from 'axios';
import { useUserStore } from '@/store/user';
import { ElMessage } from 'element-plus';

export interface ApiResponse<T = unknown> {
  code: number;
  msg: string;
  data: T;
}

const instance: AxiosInstance = axios.create({
  baseURL: '/api',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json;charset=utf-8'
  }
});

/**
 * 开发环境日志（生产环境自动禁用）
 * 仅记录URL和方法，不打印敏感数据（password/token等）
 */
const devLog = (type: 'request' | 'response' | 'error', info: Record<string, any>) => {
  if (import.meta.env.DEV) {
    const icon = type === 'request' ? '🚀' : type === 'response' ? '✅' : '❌';
    // 脱敏：不打印完整的data和headers
    const safeInfo = {
      url: info.url,
      method: info.method || undefined,
      status: info.status || undefined,
      // 仅在错误时显示简短信息
      message: type === 'error' ? info.message : undefined
    };
    console.log(`${icon} [${type.toUpperCase()}]`, JSON.stringify(safeInfo).replace(/[{}"]/g, ''));
  }
};

// 401 时尝试自动登录，避免“二次跳转登录页”打断 AI/分析流程
let autoLoginPromise: Promise<void> | null = null;

const ensureAutoLogin = async () => {
  const userStore = useUserStore();
  if (userStore.isLogin) return;

  if (!autoLoginPromise) {
    autoLoginPromise = (async () => {
      const user = await instance.post('/auth/auto-login', {}, { __skipAutoLoginRetry: true } as any) as any;
      userStore.setUser(user);
    })().catch((e) => {
      autoLoginPromise = null;
      throw e;
    });
  }

  return autoLoginPromise;
};

instance.interceptors.request.use(
  (config) => {
    // 开发环境日志（已脱敏）
    devLog('request', { 
      url: config.url, 
      method: config.method?.toUpperCase() 
    });
    
    const userStore = useUserStore();
    if (userStore.token) {
      config.headers = config.headers || {};
      (config.headers as any)['Authorization'] = `Bearer ${userStore.token}`;
    }
    return config;
  },
  async (error) => {
    devLog('error', { message: error.message });
    return Promise.reject(error);
  }
);

instance.interceptors.response.use(
  (response) => {
    // 开发环境日志（仅记录状态码，不打印完整响应体）
    devLog('response', { 
      url: response.config.url, 
      status: response.status 
    });
    
    // File download: let browser handle blob/arraybuffer directly
    if (response.config && (response.config.responseType === 'blob' || response.config.responseType === 'arraybuffer')) {
      return response.data;
    }
    const res = response.data;
    if (res.code !== 0) {
      ElMessage.error(res.msg || '请求失败');
      return Promise.reject(res);
    }
    return res.data;
  },
  async (error) => {
    // 统一错误处理：网络超时/服务器错误等
    const message = error.response?.data?.msg || error.message || '网络错误';
    devLog('error', { message });
    
    // 根据HTTP状态码提供更友好的提示
    if (error.response) {
      const status = error.response.status;
      if (status === 401) {
        const config = error.config as any;
        const url = config?.url || '';
        const skipRetry = !!config?.__skipAutoLoginRetry;
        const isAuthRelated =
          url.includes('/auth/auto-login') ||
          // [安全优化] default-credentials 接口已注释，保留判断以防误调用
          url.includes('/auth/default-credentials') ||
          url.includes('/auth/login') ||
          url.includes('/auth/register');

        // 避免在自动登录接口失败时再次触发自动登录（防止递归）
        if (!skipRetry && !isAuthRelated) {
          // 标记：本次请求已尝试自动登录重试，避免无限循环
          if (!config.__autoLoginRetried) {
            config.__autoLoginRetried = true;
            try {
              await ensureAutoLogin();
              if (useUserStore().isLogin) {
                return instance.request(config);
              }
            } catch (_) {
              // 自动登录失败，继续走兜底跳转逻辑
            }
          }

          // 兜底：自动登录失败才跳到登录页
          // [安全优化] 移除 prefillLoginCredentials，不再自动填充密码
          // 动态导入 router 实现SPA内跳转，避免整页刷新丢失状态
          // 注释旧方案: window.location.href = '/login';
          const { default: appRouter } = await import('@/router');
          appRouter.push('/login');
        }

        // 二次认证（强制登出并跳转登录页）对“AI 分析”属于多余流程。
        // 这里按你的要求：不要删除逻辑，只注释掉不使用。
        // const userStore = useUserStore();
        // userStore.logout();
        // window.location.href = '/login';

        ElMessage.error('登录已过期，请重新登录');
      } else if (status === 403) {
        ElMessage.error('没有权限执行此操作');
      } else if (status === 500) {
        ElMessage.error('服务器内部错误，请稍后重试');
      } else {
        ElMessage.error(message);
      }
    } else if (error.code === 'ECONNABORTED') {
      ElMessage.error('请求超时，请检查网络连接');
    } else {
      ElMessage.error(message);
    }
    return Promise.reject(error);
  }
);

export function createSseRequest(url: string, data: Record<string, any>): EventSource {
  const userStore = useUserStore();
  const params = new URLSearchParams();
  Object.keys(data).forEach(key => {
    params.append(key, String(data[key]));
  });
  
  const fullUrl = url + (url.includes('?') ? '&' : '?') + params.toString();
  
  const eventSource = new EventSource(fullUrl, {
    headers: {
      'Authorization': `Bearer ${userStore.token}`,
      'Accept': 'text/event-stream'
    }
  } as EventSourceInit);
  
  return eventSource;
}

export function createSseFetch(url: string, data: Record<string, any>): Promise<Response> {
  const userStore = useUserStore();
  return fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json;charset=UTF-8',
      'Authorization': `Bearer ${userStore.token}`
    },
    body: JSON.stringify(data)
  });
}

export default instance;
