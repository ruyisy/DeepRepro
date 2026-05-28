// Workflow types

export type WorkflowStatus = 'idle' | 'running' | 'completed' | 'error' | 'cancelled';

export interface WorkflowStep {
  id: string;
  title: string;
  subtitle: string;
  progress: number;
  status: 'pending' | 'active' | 'completed' | 'error';
}

export interface WorkflowTask {
  taskId: string;
  status: WorkflowStatus;
  progress: number;
  message: string;
  result?: Record<string, unknown>;
  error?: string;
  startedAt?: string;
  completedAt?: string;
}

export interface WorkflowInput {
  type: 'paper-to-code';
  inputSource: string;
  inputType: 'file' | 'url';
  enableIndexing: boolean;
}

// Workflow step definitions
export const PAPER_TO_CODE_STEPS: WorkflowStep[] = [
  { id: 'workspace', title: 'Workspace', subtitle: 'Prepare lab', progress: 5, status: 'pending' },
  { id: 'paper', title: 'Paper Analysis', subtitle: 'Extract method', progress: 15, status: 'pending' },
  { id: 'plan', title: 'Planning', subtitle: 'File tree and roadmap', progress: 40, status: 'pending' },
  { id: 'reference', title: 'Reference & Indexing', subtitle: 'Optional code memory', progress: 65, status: 'pending' },
  { id: 'implement', title: 'Implementation', subtitle: 'Planner/executor loop', progress: 85, status: 'pending' },
  { id: 'quality', title: 'Final Gate', subtitle: 'Report and diagnostics', progress: 95, status: 'pending' },
];
