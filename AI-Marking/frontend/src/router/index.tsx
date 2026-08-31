import { lazy } from 'react';
import { createBrowserRouter, Navigate } from 'react-router-dom';
import { Database, FileCheck2, FlaskConical, Terminal } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import MainLayout from '@/layouts/MainLayout';
import RouteErrorPage from '@/components/RouteErrorPage';

const R21ProjectsPage = lazy(() => import('@/pages/R21ProjectsPage'));
const R21ProjectPage = lazy(() => import('@/pages/R21ProjectPage'));
const R21PromptConfigPage = lazy(() => import('@/pages/R21PromptConfigPage'));
const R21RunnerConfigPage = lazy(() => import('@/pages/R21RunnerConfigPage'));
const R23ProjectsPage = lazy(() => import('@/pages/R23ProjectsPage'));
const R23ProjectPage = lazy(() => import('@/pages/R23ProjectPage'));
const R23RubricPage = lazy(() => import('@/pages/R23RubricPage'));
const R23RunnerConfigPage = lazy(() => import('@/pages/R23RunnerConfigPage'));
const R23DataPage = lazy(() => import('@/pages/R23DataPage'));

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
  { path: '/dress', label: 'DREsS 实验', icon: FlaskConical },
  { path: '/dress/rubric', label: 'Rubric 配置', icon: FileCheck2 },
  { path: '/dress/runners', label: 'Codex Runner', icon: Terminal },
  { path: '/dress/data', label: '数据与审计', icon: Database },
];

export const router = createBrowserRouter([
  {
    path: '/',
    element: <MainLayout />,
    errorElement: <RouteErrorPage />,
    children: [
      { index: true, element: <Navigate to="/dress" replace /> },
      {
        path: 'dress',
        element: <R23ProjectsPage />,
        handle: { label: 'DREsS 实验' } satisfies AppRouteHandle,
      },
      {
        path: 'dress/rubric',
        element: <R23RubricPage />,
        handle: {
          label: 'Rubric 配置',
          parent: { label: 'DREsS 实验', to: '/dress' },
        } satisfies AppRouteHandle,
      },
      {
        path: 'dress/runners',
        element: <R23RunnerConfigPage />,
        handle: {
          label: 'Codex Runner',
          parent: { label: 'DREsS 实验', to: '/dress' },
        } satisfies AppRouteHandle,
      },
      {
        path: 'dress/data',
        element: <R23DataPage />,
        handle: {
          label: '数据与审计',
          parent: { label: 'DREsS 实验', to: '/dress' },
        } satisfies AppRouteHandle,
      },
      {
        path: 'dress/:projectId',
        element: <R23ProjectPage />,
        handle: {
          label: 'r23 项目详情',
          parent: { label: 'DREsS 实验', to: '/dress' },
        } satisfies AppRouteHandle,
      },
      {
        path: 'research',
        element: <R21ProjectsPage />,
        handle: { label: '研究项目' } satisfies AppRouteHandle,
      },
      {
        path: 'research/:projectId',
        element: <R21ProjectPage />,
        handle: {
          label: '项目详情',
          parent: { label: '研究项目', to: '/research' },
        } satisfies AppRouteHandle,
      },
      {
        path: 'prompts',
        element: <R21PromptConfigPage />,
        handle: { label: '提示词配置' } satisfies AppRouteHandle,
      },
      {
        path: 'models',
        element: <R21RunnerConfigPage />,
        handle: { label: '模型配置' } satisfies AppRouteHandle,
      },
      { path: '*', element: <Navigate to="/" replace /> },
    ],
  },
]);
