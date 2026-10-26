import { createRouter, createWebHistory, RouteRecordRaw } from 'vue-router';
import { useUserStore } from '@/store/user';
import { autoLoginApi } from '@/api/auth';

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/Login.vue')
  },
  {
    path: '/',
    component: () => import('@/layouts/MainLayout.vue'),
    children: [
      {
        path: '',
        name: 'Home',
        component: () => import('@/views/Home.vue')
      },
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('@/views/Dashboard.vue')
      },
      {
        path: 'jobs',
        name: 'JobList',
        component: () => import('@/views/JobList.vue')
      },
      {
        path: 'data',
        name: 'DataManage',
        component: () => import('@/views/DataManage.vue')
      },
      {
        path: 'compare',
        name: 'DataCompare',
        component: () => import('@/views/DataCompare.vue')
      },
      {
        path: 'analysis',
        name: 'Analysis',
        component: () => import('@/views/Analysis.vue')
      },
      {
        path: 'settings',
        name: 'Settings',
        component: () => import('@/views/Settings.vue')
      },
      {
        path: 'ai',
        name: 'AIChat',
        component: () => import('@/views/AIChat.vue')
      },
      {
        path: 'report',
        name: 'Report',
        component: () => import('@/views/Report.vue')
      },
      {
        path: 'skill-profile',
        name: 'SkillProfile',
        component: () => import('@/views/SkillProfile.vue')
      },
      {
        path: 'admin/users',
        name: 'UserManagement',
        component: () => import('@/views/admin/UserManagement.vue'),
        meta: { requiresAdmin: true }
      },
      {
        path: 'admin/logs',
        name: 'LogAudit',
        component: () => import('@/views/admin/LogAudit.vue'),
        meta: { requiresAdmin: true }
      },
      {
        path: 'admin/knowledge',
        name: 'KnowledgeBase',
        component: () => import('@/views/admin/KnowledgeBase.vue'),
        meta: { requiresAdmin: true }
      },
      {
        path: 'admin/models',
        name: 'ModelManager',
        component: () => import('@/views/admin/ModelManager.vue'),
        meta: { requiresAdmin: true }
      }
    ]
  },
  // [优化] 添加404通配路由，访问不存在的路径时重定向到首页
  {
    path: '/:pathMatch(.*)*',
    redirect: '/'
  }
];

const router = createRouter({
  history: createWebHistory(),
  routes
});

let autoLoginPromise: Promise<void> | null = null;

router.beforeEach(async (to, from, next) => {
  const userStore = useUserStore();
  // Ensure token is loaded before auth guard runs (refresh/back-button scenario)
  userStore.loadFromStorage();

  // 未登录且访问非登录页：尝试自动登录或跳转登录
  if (!userStore.isLogin && to.path !== '/login') {
    if (!autoLoginPromise) {
      autoLoginPromise = (async () => {
        const user = await autoLoginApi();
        userStore.setUser(user);
      })();
    }

    try {
      await autoLoginPromise;
      if (!userStore.isLogin) {
        next('/login');
        return;
      }
    } catch (_) {
      autoLoginPromise = null;
      next('/login');
      return;
    }
  }

  // 检查管理员权限
  if (to.meta?.requiresAdmin && userStore.role !== 'ADMIN') {
    // 非管理员访问管理员页面，跳转到首页
    next('/');
    return;
  }

  next();
});

export default router;

