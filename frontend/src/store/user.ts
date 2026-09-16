import { defineStore } from 'pinia';

export interface UserState {
  id: number;
  username: string;
  role: string;
  email: string;
  token: string;
}

export const useUserStore = defineStore('user', {
  state: () => {
    const str = localStorage.getItem('user');
    if (str) {
      try {
        return { user: JSON.parse(str) as UserState };
      } catch (e) {
        console.error('解析用户信息失败:', e);
        localStorage.removeItem('user');
        return { user: null };
      }
    }
    return { user: null };
  },
  getters: {
    isLogin: (state) => !!state.user?.token,
    token: (state) => state.user?.token || '',
    username: (state) => state.user?.username || '',
    userId: (state) => state.user?.id || 0,
    role: (state) => state.user?.role || ''
  },
  actions: {
    setUser(user: UserState) {
      this.user = user;
      localStorage.setItem('user', JSON.stringify(user));
    },
    loadFromStorage() {
      const str = localStorage.getItem('user');
      if (str) {
        try {
          this.user = JSON.parse(str);
        } catch (e) {
          console.error('加载用户信息失败:', e);
          this.user = null;
          localStorage.removeItem('user');
        }
      }
    },
    logout() {
      this.user = null;
      localStorage.removeItem('user');
    },
    updateUser(partialUser: Partial<UserState>) {
      if (this.user) {
        this.user = { ...this.user, ...partialUser };
        localStorage.setItem('user', JSON.stringify(this.user));
      }
    }
  }
});

