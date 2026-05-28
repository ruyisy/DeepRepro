import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type {
  WorkflowStatus,
  WorkflowStep,
} from '../types/workflow';

// Activity log entry type
interface ActivityLogEntry {
  id: string;
  timestamp: Date;
  message: string;
  progress: number;
  type: 'info' | 'success' | 'warning' | 'error' | 'progress';
}

export interface DeepReproEvent {
  id: string;
  timestamp: Date;
  event: string;
  schemaVersion?: number;
  payload: Record<string, unknown>;
}

export interface RoundTrace {
  roundId: number;
  mode?: string;
  plannedFiles: string[];
  repairFile?: string;
  referenceSearches: Array<Record<string, unknown>>;
  completedFiles: string[];
  diagnostics: string[];
  status: 'running' | 'completed';
}

// User-in-Loop interaction types
export interface PendingInteraction {
  type: string;  // 'requirement_questions' | 'plan_review' | etc.
  title: string;
  description: string;
  data: {
    questions?: Array<{
      id: string;
      question: string;
      category?: string;
      importance?: string;
      hint?: string;
    }>;
    plan?: string;
    plan_preview?: string;
    original_input?: string;
    [key: string]: unknown;
  };
  options: Record<string, string>;
  required: boolean;
}

interface WorkflowState {
  // Current task
  activeTaskId: string | null;
  workflowType: 'paper-to-code' | null;
  status: WorkflowStatus;
  progress: number;
  message: string;
  startedAt: number | null;
  completedAt: number | null;

  // Steps
  steps: WorkflowStep[];
  currentStepIndex: number;

  // Streaming data
  streamedCode: string;
  currentFile: string | null;
  generatedFiles: string[];

  // Activity logs
  activityLogs: ActivityLogEntry[];
  deepReproEvents: DeepReproEvent[];
  roundTraces: RoundTrace[];
  agentState: {
    planner: 'idle' | 'active';
    executor: 'idle' | 'active';
    memory: 'idle' | 'active';
    activeRole: 'idle' | 'planner' | 'executor' | 'memory';
    message: string;
    memoryMessage: string;
  };
  fileProgress: {
    implemented: number;
    total: number;
    remaining: number;
    currentFile: string | null;
    allFiles: string[];
  };
  currentStage: string;
  artifacts: {
    finalReportPath: string | null;
    implementationReportPath: string | null;
    implementationResultPath: string | null;
    codeDirectory: string | null;
  };

  // User-in-Loop interaction
  pendingInteraction: PendingInteraction | null;
  isWaitingForInput: boolean;

  // Results
  result: Record<string, unknown> | null;
  error: string | null;

  // Recovery
  needsRecovery: boolean;  // Flag to indicate if we need to recover a task

  // Actions
  setActiveTask: (taskId: string | null, workflowType?: 'paper-to-code') => void;
  setStatus: (status: WorkflowStatus) => void;
  updateProgress: (progress: number, message: string) => void;
  setSteps: (steps: WorkflowStep[]) => void;
  updateStepStatus: (stepId: string, status: WorkflowStep['status']) => void;
  appendStreamedCode: (chunk: string) => void;
  setCurrentFile: (filename: string | null) => void;
  addGeneratedFile: (filename: string) => void;
  addActivityLog: (message: string, progress: number, type?: ActivityLogEntry['type']) => void;
  addDeepReproEvent: (event: string, payload: Record<string, unknown>, schemaVersion?: number) => void;
  setPendingInteraction: (interaction: PendingInteraction | null) => void;
  clearInteraction: () => void;
  setResult: (result: Record<string, unknown> | null) => void;
  setError: (error: string | null) => void;
  setNeedsRecovery: (needs: boolean) => void;
  reset: () => void;
}

const initialState = {
  activeTaskId: null,
  workflowType: null as 'paper-to-code' | null,
  status: 'idle' as WorkflowStatus,
  progress: 0,
  message: '',
  startedAt: null,
  completedAt: null,
  steps: [],
  currentStepIndex: -1,
  streamedCode: '',
  currentFile: null,
  generatedFiles: [],
  activityLogs: [] as ActivityLogEntry[],
  deepReproEvents: [] as DeepReproEvent[],
  roundTraces: [] as RoundTrace[],
  agentState: {
    planner: 'idle' as const,
    executor: 'idle' as const,
    memory: 'idle' as const,
    activeRole: 'idle' as const,
    message: '',
    memoryMessage: '',
  },
  fileProgress: { implemented: 0, total: 0, remaining: 0, currentFile: null, allFiles: [] as string[] },
  currentStage: 'idle',
  artifacts: {
    finalReportPath: null,
    implementationReportPath: null,
    implementationResultPath: null,
    codeDirectory: null,
  },
  pendingInteraction: null as PendingInteraction | null,
  isWaitingForInput: false,
  result: null,
  error: null,
  needsRecovery: false,
};

export const useWorkflowStore = create<WorkflowState>()(
  persist(
    (set, get) => ({
      ...initialState,

      setActiveTask: (taskId, workflowType) => set((state) => ({
        activeTaskId: taskId,
        workflowType: workflowType ?? state.workflowType,
        startedAt: taskId ? Date.now() : state.startedAt,
        completedAt: taskId ? null : state.completedAt,
      })),

  setStatus: (status) => {
    console.log('[workflowStore] setStatus:', status);
    const state = get();
    set({
      status,
      startedAt: status === 'running' && !state.startedAt ? Date.now() : state.startedAt,
      completedAt:
        status === 'completed' || status === 'error' || status === 'cancelled'
          ? Date.now()
          : status === 'running'
          ? null
          : state.completedAt,
    });
  },

  updateProgress: (progress, message) => {
    const { steps } = get();

    // Find current step based on progress
    let currentStepIndex = -1;
    for (let i = steps.length - 1; i >= 0; i--) {
      if (progress >= steps[i].progress) {
        currentStepIndex = i;
        break;
      }
    }

    // Check if workflow is complete (progress >= 100)
    const isComplete = progress >= 100;

    // Update step statuses
    const updatedSteps = steps.map((step, index) => ({
      ...step,
      status:
        isComplete
          ? 'completed'  // All steps completed when progress >= 100
          : index < currentStepIndex
          ? 'completed'
          : index === currentStepIndex
          ? 'active'
          : 'pending',
    })) as WorkflowStep[];

    set({
      progress,
      message,
      currentStepIndex: isComplete ? steps.length - 1 : currentStepIndex,
      steps: updatedSteps,
    });
  },

  setSteps: (steps) => set({ steps }),

  updateStepStatus: (stepId, status) => {
    const { steps } = get();
    const updatedSteps = steps.map((step) =>
      step.id === stepId ? { ...step, status } : step
    );
    set({ steps: updatedSteps });
  },

  appendStreamedCode: (chunk) =>
    set((state) => ({
      streamedCode: state.streamedCode + chunk,
    })),

  setCurrentFile: (filename) => set({ currentFile: filename }),

  addGeneratedFile: (filename) =>
    set((state) => ({
      generatedFiles: [...state.generatedFiles, filename],
    })),

  addActivityLog: (message, progress, type = 'progress') =>
    set((state) => ({
      activityLogs: [
        ...state.activityLogs,
        {
          id: `log-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
          timestamp: new Date(),
          message,
          progress,
          type,
        },
      ],
    })),

  addDeepReproEvent: (event, payload, schemaVersion) =>
    set((state) => {
      const nextEvent: DeepReproEvent = {
        id: `deeprepro-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
        timestamp: new Date(),
        event,
        schemaVersion,
        payload,
      };

      let roundTraces = state.roundTraces;
      let agentState = state.agentState;
      let fileProgress = state.fileProgress;
      let currentStage = state.currentStage;
      let artifacts = state.artifacts;

      if (event === 'stage') {
        currentStage = String(payload.stage || state.currentStage);
      }

      if (event === 'agent_state') {
        agentState = {
          ...state.agentState,
          planner: payload.planner === 'active' ? 'active' : 'idle',
          executor: payload.executor === 'active' ? 'active' : 'idle',
          activeRole:
            payload.active_role === 'planner' ||
            payload.active_role === 'executor'
              ? payload.active_role
              : state.agentState.memory === 'active'
              ? 'memory'
              : 'idle',
          message: String(payload.message || ''),
        };
      }

      if (event === 'memory_state') {
        const memoryActive = payload.status === 'active';
        agentState = {
          ...state.agentState,
          memory: memoryActive ? 'active' : 'idle',
          activeRole: memoryActive ? 'memory' : state.agentState.activeRole === 'memory' ? 'idle' : state.agentState.activeRole,
          memoryMessage: String(payload.message || ''),
        };
      }

      if (event === 'round_start') {
        const roundId = Number(payload.round_id || 0);
        const plannedFiles = Array.isArray(payload.planned_files)
          ? payload.planned_files.map(String)
          : [];
        const allFiles = Array.isArray(payload.all_files)
          ? payload.all_files.map(String)
          : state.fileProgress.allFiles;
        const referenceSearches = Array.isArray(payload.reference_searches)
          ? payload.reference_searches as Array<Record<string, unknown>>
          : [];
        const nextRound: RoundTrace = {
          roundId,
          mode: String(payload.mode || ''),
          plannedFiles,
          repairFile: String(payload.repair_file || ''),
          referenceSearches,
          completedFiles: [],
          diagnostics: [],
          status: 'running',
        };
        roundTraces = [
          ...state.roundTraces.filter((round) => round.roundId !== roundId),
          nextRound,
        ].sort((a, b) => a.roundId - b.roundId);
        fileProgress = {
          implemented: Number(payload.implemented_files_count ?? state.fileProgress.implemented),
          total: Number(payload.total_files ?? state.fileProgress.total),
          remaining: Number(payload.remaining_files_count ?? state.fileProgress.remaining),
          currentFile: state.fileProgress.currentFile,
          allFiles,
        };
      }

      if (event === 'file_progress') {
        const allFiles = Array.isArray(payload.all_files)
          ? payload.all_files.map(String)
          : state.fileProgress.allFiles;
        fileProgress = {
          implemented: Number(payload.implemented_files_count ?? state.fileProgress.implemented),
          total: Number(payload.total_files ?? state.fileProgress.total),
          remaining: Number(payload.remaining_files_count ?? state.fileProgress.remaining),
          currentFile: payload.current_file ? String(payload.current_file) : state.fileProgress.currentFile,
          allFiles,
        };
        const roundId = Number(payload.round_id || 0);
        const completed = Array.isArray(payload.completed_files_this_round)
          ? payload.completed_files_this_round.map(String)
          : [];
        roundTraces = state.roundTraces.map((round) =>
          round.roundId === roundId
            ? { ...round, completedFiles: Array.from(new Set([...round.completedFiles, ...completed])) }
            : round
        );
      }

      if (event === 'round_done') {
        const roundId = Number(payload.round_id || 0);
        const allFiles = Array.isArray(payload.all_files)
          ? payload.all_files.map(String)
          : state.fileProgress.allFiles;
        fileProgress = {
          implemented: Number(payload.implemented_files_count ?? state.fileProgress.implemented),
          total: Number(payload.total_files ?? state.fileProgress.total),
          remaining: Number(payload.remaining_files_count ?? state.fileProgress.remaining),
          currentFile: null,
          allFiles,
        };
        const completed = Array.isArray(payload.completed_files)
          ? payload.completed_files.map(String)
          : [];
        const diagnostics = Array.isArray(payload.diagnostics)
          ? payload.diagnostics.map(String)
          : [];
        roundTraces = state.roundTraces.map((round) =>
          round.roundId === roundId
            ? {
                ...round,
                plannedFiles:
                  round.mode === 'fast' && round.roundId === 1 && completed.length > 0
                    ? Array.from(new Set(completed))
                    : round.plannedFiles,
                completedFiles: Array.from(new Set([...round.completedFiles, ...completed])),
                diagnostics,
                status: 'completed',
              }
            : round
        );
      }

      if (event === 'artifact') {
        artifacts = {
          finalReportPath: payload.final_report_path ? String(payload.final_report_path) : state.artifacts.finalReportPath,
          implementationReportPath: payload.implementation_report_path
            ? String(payload.implementation_report_path)
            : state.artifacts.implementationReportPath,
          implementationResultPath: payload.implementation_result_path
            ? String(payload.implementation_result_path)
            : state.artifacts.implementationResultPath,
          codeDirectory: payload.code_directory ? String(payload.code_directory) : state.artifacts.codeDirectory,
        };
      }

      return {
        deepReproEvents: [...state.deepReproEvents, nextEvent].slice(-120),
        roundTraces,
        agentState,
        fileProgress,
        currentStage,
        artifacts,
      };
    }),

  setPendingInteraction: (interaction) => {
    console.log('[workflowStore] setPendingInteraction:', interaction?.type);
    set({
      pendingInteraction: interaction,
      isWaitingForInput: interaction !== null,
    });
  },

  clearInteraction: () => {
    console.log('[workflowStore] clearInteraction');
    set({
      pendingInteraction: null,
      isWaitingForInput: false,
    });
  },

  setResult: (result) => {
    console.log('[workflowStore] setResult:', result);
    set({ result });
  },

  setError: (error) => set({ error, status: error ? 'error' : get().status }),

  setNeedsRecovery: (needs) => set({ needsRecovery: needs }),

  reset: () => {
    console.log('[workflowStore] Resetting state and clearing localStorage');
    // Clear localStorage explicitly to ensure clean state
    try {
      localStorage.removeItem('deeprepro-workflow');
    } catch (e) {
      console.error('[workflowStore] Failed to clear localStorage:', e);
    }
    set(initialState);
  },
    }),
    {
      name: 'deeprepro-workflow',
      // Only persist task-related data for recovery when task is running or waiting
      partialize: (state) => {
        const isActive = state.status === 'running' || state.isWaitingForInput;
        return {
          // Only persist activeTaskId if task is still running or waiting for input
          // This prevents trying to recover completed/errored tasks
          activeTaskId: isActive ? state.activeTaskId : null,
          workflowType: isActive ? state.workflowType : null,
          status: isActive ? state.status : 'idle',
          progress: isActive ? state.progress : 0,
          startedAt: isActive ? state.startedAt : null,
          completedAt: isActive ? state.completedAt : null,
          steps: isActive ? state.steps : [],
          isWaitingForInput: state.isWaitingForInput,
        };
      },
    }
  )
);
