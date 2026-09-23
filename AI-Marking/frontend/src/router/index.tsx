import { lazy } from 'react';
import { createBrowserRouter, Navigate } from 'react-router-dom';
import { BrainCircuit, Cog } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import MainLayout from '@/layouts/MainLayout';
import RouteErrorPage from '@/components/RouteErrorPage';

const MemoryStudyPage = lazy(() => import('@/pages/MemoryStudyPage'));
const MemoryStudyDetailPage = lazy(
  () => import('@/pages/MemoryStudyDetailPage'),
);
const MemoryStudyConfigPage = lazy(
  () => import('@/pages/MemoryStudyConfigPage'),
);

export interface NavItem {
  path: string;
  label: string;
  icon: LucideIcon;
}

export interface AppRouteHandle {
  label: string;
  parent?: {
    label: string;
    to: string;
  };
}

/** 主导航数据源：MainLayout 与路由表共享，避免双重维护 */
export const navItems: NavItem[] = [
  { path: '/memory-study', label: 'SAF 记忆研究', icon: BrainCircuit },
  { path: '/memory-study/config', label: '模型配置', icon: Cog },
];

export const router = createBrowserRouter([
  {
    path: '/',
    element: <MainLayout />,
    errorElement: <RouteErrorPage />,
    children: [
      { index: true, element: <Navigate to="/memory-study" replace /> },
      {
        path: 'memory-study',
        element: <MemoryStudyPage />,
        handle: { label: 'SAF 记忆研究' } satisfies AppRouteHandle,
      },
      {
        path: 'memory-study/:studyId',
        element: <MemoryStudyDetailPage />,
        handle: {
          label: '记忆研究运行台',
          parent: { label: 'SAF 记忆研究', to: '/memory-study' },
        } satisfies AppRouteHandle,
      },
      {
        path: 'memory-study/config',
        element: <MemoryStudyConfigPage />,
        handle: {
          label: '模型配置',
          parent: { label: 'SAF 记忆研究', to: '/memory-study' },
        } satisfies AppRouteHandle,
      },
      { path: '*', element: <Navigate to="/" replace /> },
    ],
  },
]);