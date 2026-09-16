import http from './http';

export interface LoginPayload {
  username: string;
  password: string;
}

export interface RegisterPayload {
  username: string;
  password: string;
  email: string;
}

export interface UserVO {
  id: number;
  username: string;
  role: string;
  email: string;
  token: string;
}

export interface DefaultCredentialsVO {
  username: string;
  password: string;
}

export function loginApi(data: LoginPayload): Promise<UserVO> {
  return http.post('/auth/login', data);
}

export function registerApi(data: RegisterPayload): Promise<void> {
  return http.post('/auth/register', data);
}

/**
 * 自动登录：使用后端默认用户凭证获取新的 JWT。
 * 该接口通常仅在开发/本地场景启用（后端已做生产禁用）。
 */
export function autoLoginApi(): Promise<UserVO> {
  return http.post('/auth/auto-login', {});
}

/**
 * 获取默认用户名（仅用户名，不暴露密码）
 * 用于登录页表单预填，用户仍需手动输入密码。
 */
export function defaultUsernameApi(): Promise<{ username: string }> {
  return http.get('/auth/default-username');
}

/**
 * [安全优化] 已注释：默认账号密码接口
 * 原接口会将密码暴露给前端，现通过 auto-login 实现免登录，无需暴露密码。
 * 如需恢复，请同时取消后端 SecurityConfig 和 AuthController 中的注释。
 */
// export function defaultCredentialsApi(): Promise<DefaultCredentialsVO> {
//   return http.get('/auth/default-credentials');
// }

