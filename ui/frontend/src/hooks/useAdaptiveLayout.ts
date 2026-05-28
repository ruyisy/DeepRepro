import { useMemo } from 'react';
import type { TaskType, LayoutConfig } from '../types/common';

const layoutConfigs: Record<TaskType, LayoutConfig> = {
  'paper-to-code': {
    sidebarWidth: 320,
    showCodePreview: true,
    showWorkflowCanvas: true,
    splitRatio: 0.6,
  },
  settings: {
    sidebarWidth: 280,
    showCodePreview: false,
    showWorkflowCanvas: false,
    splitRatio: 1,
  },
};

export function useAdaptiveLayout(taskType: TaskType): LayoutConfig {
  return useMemo(() => layoutConfigs[taskType], [taskType]);
}
