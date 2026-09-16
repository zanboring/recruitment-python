import http from '@/api/http';

export interface UserVO {
  id: number;
  username: string;
  role: string;
  email?: string;
  enabled: boolean;
  createdAt?: string;
  skills?: string;
  education?: string;
  experienceYears?: number;
}

export interface UserPageVO {
  list: UserVO[];
  total: number;
  pageNum: number;
  pageSize: number;
}

export const userApi = {
  listUsers: (username?: string, page = 1, pageSize = 10): Promise<UserPageVO> => {
    return http.get('/users', {
      params: { username, pageNum: page, pageSize },
    });
  },

  getUserById: (id: number): Promise<UserVO> => {
    return http.get(`/users/${id}`);
  },

  updateUser: (id: number, data: Partial<UserVO>): Promise<void> => {
    return http.put(`/users/${id}`, data);
  },

  deleteUser: (id: number): Promise<void> => {
    return http.delete(`/users/${id}`);
  },

  toggleUserStatus: (id: number, enabled: boolean): Promise<void> => {
    return http.patch(`/users/${id}/status`, null, {
      params: { enabled },
    });
  },
};
