import { useEffect, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import {
  AlertCircle,
  BookOpen,
  Bot,
  Brain,
  CheckCircle,
  CircleDot,
  Code2,
  Eye,
  File,
  FileText,
  FolderOpen,
  Lightbulb,
  ImagePlus,
  Layers3,
  Loader2,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  StopCircle,
  Users,
  UploadCloud,
  Wrench,
  Zap,
} from 'lucide-react';
import { Button } from '../components/common';
import { ConfirmDialog } from '../components/common/ConfirmDialog';
import { FileUploader, UrlInput } from '../components/input';
import { useWorkflowStore, type RoundTrace } from '../stores/workflowStore';
import { useStreaming } from '../hooks/useStreaming';
import { workflowsApi } from '../services/api';
import { toast } from '../components/common/Toaster';
import { PAPER_TO_CODE_STEPS, type WorkflowStep } from '../types/workflow';

type InputMethod = 'file' | 'url';
type WorkflowMode = 'raw_fast' | 'infer_fast' | 'raw_deepplan' | 'infer_deepplan';
type PlanningImageFile = {
  fileId: string;
  path: string;
};
type UploadedPaperFile = {
  fileId: string;
  path: string;
  name: string;
};
type BatchQueueItem = {
  taskId: string;
  label: string;
  status: 'queued' | 'running' | 'completed' | 'error';
};

function BatchQueuePanel({ items }: { items: BatchQueueItem[] }) {
  if (!items.length) return null;

  return (
    <div className="rounded-[2rem] border border-stone-200 bg-white/70 p-5 shadow-sm">
      <div className="mb-3 flex items-center justify-between">
        <div>
          <p className="text-sm font-semibold text-stone-950">Batch queue</p>
          <p className="text-xs text-stone-500">Runs in upload order.</p>
        </div>
        <span className="rounded-full bg-stone-100 px-2.5 py-1 text-xs text-stone-500">
          {items.length}
        </span>
      </div>
      <div className="space-y-2">
        {items.map((item, index) => (
          <div
            key={item.taskId}
            className={`flex items-center justify-between rounded-2xl border px-3 py-2 text-sm ${
              item.status === 'running'
                ? 'border-emerald-300 bg-emerald-50 text-emerald-900'
                : item.status === 'completed'
                ? 'border-stone-200 bg-stone-50 text-stone-500'
                : item.status === 'error'
                ? 'border-red-200 bg-red-50 text-red-800'
                : 'border-stone-200 bg-white text-stone-500'
            }`}
          >
            <div className="min-w-0">
              <p className="truncate font-medium">
                {index + 1}. {item.label}
              </p>
              <p className="text-xs uppercase tracking-[0.16em] opacity-70">{item.taskId.slice(0, 8)}</p>
            </div>
            <span className="rounded-full bg-white/70 px-2 py-1 text-[11px] font-semibold uppercase tracking-[0.16em]">
              {item.status}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

const WORKFLOW_MODE_OPTIONS: Array<{
  value: WorkflowMode;
  label: string;
  short: string;
  description: string;
  badge: string;
  icon: typeof Zap;
}> = [
  {
    value: 'raw_fast',
    label: 'Raw Fast',
    short: 'fast / no index',
    description: 'Batched multi-file execution with post-round next-step planning.',
    badge: 'Baseline',
    icon: Zap,
  },
  {
    value: 'infer_fast',
    label: 'Reference Fast',
    short: 'fast / indexed',
    description: 'Original fast execution plus optional reference index search.',
    badge: 'Reference',
    icon: Search,
  },
  {
    value: 'raw_deepplan',
    label: 'DeepPlan',
    short: 'planner / no index',
    description: 'A planner model writes precise subplans before executor rounds.',
    badge: 'Planner',
    icon: Bot,
  },
  {
    value: 'infer_deepplan',
    label: 'DeepPlan + Reference',
    short: 'planner / indexed',
    description: 'Planner/executor collaboration with reference-guided execution.',
    badge: 'Full',
    icon: Layers3,
  },
];

const DEEPREPRO_FEATURES: Array<{
  title: string;
  description: string;
  icon: typeof Users;
}> = [
  {
    title: 'Multi-agent collaboration',
    description: 'Planner, executor, reference analysis, and diagnostics cooperate in one workflow.',
    icon: Users,
  },
  {
    title: 'Deep Subplanning',
    description: 'DeepPlan turns paper-level goals into round-level subplans, repairs, and file guidance.',
    icon: Layers3,
  },
  {
    title: 'Automatic issue repair',
    description: 'Prioritizes high-risk files using summaries, diagnostics, and lightweight checks.',
    icon: ShieldCheck,
  },
  {
    title: 'Efficient memory management',
    description: 'Keeps compressed memory summaries flowing without blocking execution.',
    icon: Eye,
  },
];
const STAGE_META: Record<string, { title: string; description: string; icon: typeof CircleDot }> = {
  workspace: { title: 'Workspace', description: 'Prepare reproducible workspace', icon: FolderOpen },
  paper_analysis: { title: 'Paper Analysis', description: 'Parse the paper into method signals', icon: BookOpen },
  planning: { title: 'Planning', description: 'Generate file tree and roadmap', icon: FileText },
  reference_indexing: { title: 'Reference & Indexing', description: 'Fetch repositories and build code memory', icon: Search },
  implementation: { title: 'Implementation', description: 'Write, repair, and summarize files', icon: Code2 },
  idle: { title: 'Ready', description: 'Waiting for a paper', icon: CircleDot },
};

function statusPill(status: string) {
  if (status === 'running') return 'bg-emerald-100 text-emerald-800 ring-emerald-200';
  if (status === 'completed') return 'bg-stone-900 text-amber-50 ring-stone-700';
  if (status === 'error') return 'bg-red-100 text-red-800 ring-red-200';
  return 'bg-white/70 text-stone-600 ring-stone-200';
}

function findFirstStringByKey(value: unknown, keys: string[], depth = 0): string | null {
  if (!value || depth > 6) return null;
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = findFirstStringByKey(item, keys, depth + 1);
      if (found) return found;
    }
    return null;
  }
  if (typeof value !== 'object') return null;

  const record = value as Record<string, unknown>;
  for (const [key, child] of Object.entries(record)) {
    if (keys.includes(key) && typeof child === 'string' && child.trim()) {
      return child;
    }
  }
  for (const child of Object.values(record)) {
    const found = findFirstStringByKey(child, keys, depth + 1);
    if (found) return found;
  }
  return null;
}

function SupplementaryContext({
  value,
  onChange,
  disabled,
}: {
  value: string;
  onChange: (value: string) => void;
  disabled: boolean;
}) {
  return (
    <div className="rounded-[1.75rem] border border-stone-200 bg-white/70 p-5 shadow-sm backdrop-blur">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold text-stone-900">
            <Sparkles className="h-4 w-4 text-amber-600" />
            User intent
          </div>
          <p className="mt-1 text-sm leading-6 text-stone-500">
            Optional constraints for planning. The paper remains authoritative.
          </p>
        </div>
        {value.trim() && (
          <span className="rounded-full bg-amber-100 px-2.5 py-1 text-xs font-medium text-amber-800">
            Added
          </span>
        )}
      </div>
      <textarea
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
        rows={4}
        placeholder="Example: prioritize a runnable demo, preserve the paper's module names, skip expensive training, use the provided figure as the target architecture..."
        className="w-full resize-none rounded-2xl border border-stone-200 bg-stone-50/80 px-4 py-3 text-sm text-stone-800 outline-none transition placeholder:text-stone-400 focus:border-amber-400 focus:bg-white focus:ring-4 focus:ring-amber-200/50 disabled:opacity-60"
      />
    </div>
  );
}

function ImageContextCard({
  imageCount,
  onImageUploaded,
  onImageRemoved,
  disabled,
}: {
  imageCount: number;
  onImageUploaded: (fileId: string, path: string) => void;
  onImageRemoved: (fileId: string) => void;
  disabled: boolean;
}) {
  return (
    <div className="rounded-[1.75rem] border border-stone-200 bg-white/70 p-5 shadow-sm backdrop-blur">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold text-stone-900">
            <ImagePlus className="h-4 w-4 text-amber-600" />
            Figure context
          </div>
          <p className="mt-1 text-sm leading-6 text-stone-500">
            Add diagrams, screenshots, or tables as optional planning hints.
          </p>
        </div>
        <span className="rounded-full bg-stone-100 px-2.5 py-1 text-xs font-medium text-stone-500">
          {imageCount} attached
        </span>
      </div>
      <FileUploader
        onFileUploaded={onImageUploaded}
        onFileRemoved={onImageRemoved}
        acceptedTypes={['.png', '.jpg', '.jpeg', '.webp', '.bmp']}
        maxSize={20 * 1024 * 1024}
        disabled={disabled}
        multiple
        title="Drop architecture figures, tables, or screenshots"
        description="Optional planning-only images up to 20MB"
      />
    </div>
  );
}

function StageTimeline({
  steps,
  activeStageTitle,
}: {
  steps: WorkflowStep[];
  activeStageTitle: string;
}) {
  const visibleSteps = steps.length ? steps : PAPER_TO_CODE_STEPS;

  return (
    <div className="rounded-[2rem] border border-stone-200 bg-white/70 p-5 shadow-sm backdrop-blur">
      <div className="mb-5 flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-sm font-semibold text-stone-950">Stage timeline</p>
          <p className="text-xs text-stone-500">Current stage: {activeStageTitle}</p>
        </div>
      </div>
      <div className="relative">
        <div className="absolute left-4 top-4 hidden h-[calc(100%-2rem)] w-px bg-stone-200 sm:block" />
        <div className="space-y-3">
          {visibleSteps.map((step, index) => {
            const active = step.status === 'active';
            const completed = step.status === 'completed';
            return (
              <div
                key={step.id}
                className={`relative rounded-2xl border p-3 transition ${
                  active
                    ? 'border-amber-300 bg-amber-50 shadow-sm'
                    : completed
                    ? 'border-emerald-200 bg-emerald-50/70'
                    : 'border-stone-200 bg-white/70'
                }`}
              >
                <div className="flex items-center gap-3">
                  <span
                    className={`z-10 flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${
                      active
                        ? 'bg-amber-500 text-white'
                        : completed
                        ? 'bg-emerald-500 text-white'
                        : 'bg-stone-200 text-stone-500'
                    }`}
                  >
                    {completed ? <CheckCircle className="h-4 w-4" /> : index + 1}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-semibold text-stone-900">{step.title}</p>
                    <p className="text-xs text-stone-500">{step.subtitle}</p>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function AgentLamp({
  title,
  active,
  subtitle,
  icon: Icon,
  tone = 'emerald',
  muted = false,
}: {
  title: string;
  active: boolean;
  subtitle: string;
  icon: typeof Bot;
  tone?: 'cyan' | 'violet' | 'amber' | 'emerald';
  muted?: boolean;
}) {
  const toneClass = {
    cyan: {
      card: 'border-cyan-300 bg-cyan-50 shadow-[0_0_46px_rgba(34,211,238,0.22)]',
      glow: 'bg-cyan-300/40',
      bulb: 'bg-cyan-400 text-slate-950 shadow-[0_0_34px_rgba(34,211,238,0.72)]',
      dot: 'bg-cyan-400',
    },
    violet: {
      card: 'border-violet-300 bg-violet-50 shadow-[0_0_46px_rgba(139,92,246,0.2)]',
      glow: 'bg-violet-300/40',
      bulb: 'bg-violet-500 text-white shadow-[0_0_34px_rgba(139,92,246,0.68)]',
      dot: 'bg-violet-500',
    },
    amber: {
      card: 'border-amber-300 bg-amber-50 shadow-[0_0_46px_rgba(245,158,11,0.2)]',
      glow: 'bg-amber-300/40',
      bulb: 'bg-amber-400 text-slate-950 shadow-[0_0_34px_rgba(245,158,11,0.7)]',
      dot: 'bg-amber-400',
    },
    emerald: {
      card: 'border-emerald-300 bg-emerald-50 shadow-[0_0_46px_rgba(16,185,129,0.18)]',
      glow: 'bg-emerald-300/35',
      bulb: 'bg-emerald-500 text-white shadow-[0_0_34px_rgba(16,185,129,0.62)]',
      dot: 'bg-emerald-500',
    },
  }[tone];

  return (
    <div
      className={`relative overflow-hidden rounded-3xl border p-5 transition ${
        active
          ? toneClass.card
          : muted
          ? 'border-stone-200 bg-stone-100/80'
          : 'border-stone-200 bg-white/70'
      }`}
    >
      {active && <div className={`absolute right-[-2rem] top-[-2rem] h-28 w-28 rounded-full ${toneClass.glow} blur-2xl`} />}
      <div className="relative flex items-center gap-3">
        <div className="relative flex h-14 w-14 shrink-0 items-center justify-center">
          {active && <span className={`absolute h-14 w-14 animate-ping rounded-full ${toneClass.dot} opacity-20`} />}
          <div
            className={`relative flex h-12 w-12 items-center justify-center rounded-full transition ${
              active ? toneClass.bulb : muted ? 'bg-stone-200 text-stone-400' : 'bg-stone-900 text-amber-50'
            }`}
          >
            <Lightbulb className="absolute h-7 w-7 opacity-90" />
            <Icon className="h-3.5 w-3.5 translate-y-[1px] opacity-80" />
          </div>
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-stone-900">{title}</p>
          <p className="mt-1 text-xs leading-5 text-stone-500">{subtitle}</p>
        </div>
        <span
          className={`h-3 w-3 shrink-0 rounded-full ${
            active ? `animate-pulse ${toneClass.dot}` : muted ? 'bg-stone-300' : 'bg-stone-400'
          }`}
        />
      </div>
    </div>
  );
}

function MemoryBrainCard({
  active,
  subtitle,
}: {
  active: boolean;
  subtitle: string;
}) {
  return (
    <div
      className={`relative overflow-hidden rounded-3xl border p-5 transition ${
        active
          ? 'border-amber-300 bg-gradient-to-br from-amber-50 via-orange-50 to-white shadow-[0_0_44px_rgba(245,158,11,0.2)]'
          : 'border-stone-200 bg-white/70'
      }`}
    >
      {active && (
        <>
          <div className="absolute left-10 top-[-3rem] h-28 w-28 rounded-full bg-amber-300/30 blur-3xl" />
          <div className="absolute bottom-[-2rem] right-[-2rem] h-28 w-28 rounded-full bg-orange-300/25 blur-3xl" />
        </>
      )}
      <div className="relative flex items-center gap-4">
        <div
          className={`relative flex h-16 w-16 shrink-0 items-center justify-center rounded-2xl transition ${
            active
              ? 'bg-amber-400 text-stone-950 shadow-[0_0_34px_rgba(245,158,11,0.62)]'
              : 'bg-stone-100 text-stone-400'
          }`}
        >
          {active && <span className="absolute h-16 w-16 animate-ping rounded-2xl bg-amber-400 opacity-15" />}
          <Brain className="relative h-8 w-8" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-sm font-semibold text-stone-900">Memory Management Agent</p>
            <span
              className={`rounded-full px-2.5 py-1 text-[11px] font-semibold ${
                active ? 'bg-amber-200 text-amber-950' : 'bg-stone-100 text-stone-500'
              }`}
            >
              {active ? 'Summarizing' : 'Idle'}
            </span>
          </div>
          <p className="mt-1 text-xs leading-5 text-stone-500">{subtitle}</p>
        </div>
      </div>
    </div>
  );
}

function MetricCard({
  label,
  value,
  detail,
}: {
  label: string;
  value: string | number;
  detail: string;
}) {
  return (
    <div className="rounded-3xl border border-stone-200 bg-white/70 p-5 shadow-sm">
      <p className="text-xs font-semibold uppercase tracking-[0.18em] text-stone-400">{label}</p>
      <p className="mt-3 text-3xl font-semibold tracking-tight text-stone-950">{value}</p>
      <p className="mt-1 text-xs leading-5 text-stone-500">{detail}</p>
    </div>
  );
}
function ModeFlow({ isDeepPlan, isIndexed }: { isDeepPlan: boolean; isIndexed: boolean }) {
  const steps = isDeepPlan
    ? [
        { title: 'Planner Subplan', detail: 'Choose repair + 1-3 files', icon: Bot },
        { title: 'Executor Batch', detail: 'Write files step by step', icon: Code2 },
        { title: 'Diagnostics', detail: 'Catch interface and product risks', icon: ShieldCheck },
        { title: 'Planner Feedback', detail: 'Feed summary into next round', icon: RefreshCw },
      ]
    : [
        { title: 'Batch Execution', detail: 'Select and write focused file batches', icon: Zap },
        { title: 'File Summary', detail: 'Compress each generated file', icon: FileText },
        { title: 'Next Steps', detail: 'Plan after round completion', icon: RefreshCw },
        { title: isIndexed ? 'Optional References' : 'Direct Context', detail: isIndexed ? 'Search when useful' : 'No index lookup', icon: Search },
      ];

  return (
    <div className="rounded-[2rem] border border-stone-200 bg-white/75 p-5 shadow-sm backdrop-blur">
      <div className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-sm font-semibold text-stone-950">
            {isDeepPlan ? 'DeepPlan collaboration loop' : 'Fast execution loop'}
          </p>
          <p className="text-xs text-stone-500">
            {isDeepPlan
              ? 'Planner and executor exchange structured round context.'
              : 'Executor keeps the original lightweight flow with round summaries.'}
          </p>
        </div>
        <span className="w-fit rounded-full bg-stone-950 px-3 py-1 text-xs font-semibold text-amber-50">
          {isIndexed ? 'reference-aware' : 'raw'}
        </span>
      </div>
      <div className="grid gap-3 md:grid-cols-4">
        {steps.map((step, index) => {
          const Icon = step.icon;
          return (
            <div key={step.title} className="relative rounded-2xl border border-stone-200 bg-stone-50/80 p-4">
              {index < steps.length - 1 && (
                <div className="absolute -right-2 top-1/2 hidden h-px w-4 bg-stone-300 md:block" />
              )}
              <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-2xl bg-stone-950 text-amber-50">
                <Icon className="h-4 w-4" />
              </div>
              <p className="text-sm font-semibold text-stone-950">{step.title}</p>
              <p className="mt-1 text-xs leading-5 text-stone-500">{step.detail}</p>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function RoundCard({ round }: { round: RoundTrace }) {
  const hasDiagnostics = round.diagnostics.length > 0;
  const referenceCount = round.referenceSearches.length;
  const hasRepair = Boolean(round.repairFile);

  return (
    <div className="rounded-2xl border border-stone-200 bg-white/75 p-4 shadow-sm transition hover:border-stone-300">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="flex h-8 w-8 items-center justify-center rounded-full bg-stone-900 text-sm font-semibold text-amber-50">
            {round.roundId}
          </span>
          <div>
            <p className="text-sm font-semibold text-stone-900">Round {round.roundId}</p>
            <p className="text-xs text-stone-500">{round.mode || 'fast'} execution</p>
          </div>
        </div>
        <span
          className={`rounded-full px-2.5 py-1 text-xs font-medium ${
            round.status === 'completed'
              ? 'bg-emerald-100 text-emerald-800'
              : 'bg-amber-100 text-amber-800'
          }`}
        >
          {round.status}
        </span>
      </div>
      <div className="mb-3 flex flex-wrap gap-2">
        <span className="rounded-full bg-stone-100 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.16em] text-stone-600">
          {round.mode || 'fast'}
        </span>
        <span className="rounded-full bg-cyan-50 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.16em] text-cyan-700">
          {hasRepair ? 'repair-first' : 'batch-first'}
        </span>
        <span className="rounded-full bg-amber-50 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.16em] text-amber-700">
          {referenceCount} reference hints
        </span>
      </div>
      {round.repairFile && (
        <div className="mb-3 rounded-xl bg-red-50 px-3 py-2 text-xs text-red-700">
          <Wrench className="mr-1 inline h-3.5 w-3.5" />
          Repair first: {round.repairFile}
        </div>
      )}
      <div className="space-y-2">
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-stone-400">
          {round.mode === 'fast' ? 'Next-step files' : 'Planned files'}
        </p>
        <p className="text-[11px] leading-4 text-stone-400">
          {round.mode === 'fast'
            ? round.roundId === 1
              ? 'Initial fast round starts from the first remaining file.'
              : 'Selected after the previous round; completed files show what this round actually wrote.'
            : 'Selected by the planner for this round; completed files show what the executor actually wrote.'}
        </p>
        <div className="flex flex-wrap gap-2">
          {round.plannedFiles.length ? (
            round.plannedFiles.map((file) => (
              <span key={file} className="rounded-full bg-stone-100 px-2.5 py-1 text-xs text-stone-700">
                {file}
              </span>
            ))
          ) : (
            <span className="text-xs text-stone-400">No file plan received yet.</span>
          )}
        </div>
      </div>
      {round.completedFiles.length > 0 && (
        <div className="mt-3 space-y-2">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-emerald-600">
            Completed
          </p>
          {round.completedFiles.map((file) => (
            <div key={file} className="flex items-center gap-2 text-xs text-stone-600">
              <CheckCircle className="h-3.5 w-3.5 text-emerald-500" />
              <span className="truncate">{file}</span>
            </div>
          ))}
        </div>
      )}
      {round.referenceSearches.length > 0 && (
        <div className="mt-3 rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-800">
          <Search className="mr-1 inline h-3.5 w-3.5" />
          {round.referenceSearches.length} reference hint{round.referenceSearches.length > 1 ? 's' : ''}
        </div>
      )}
      {hasDiagnostics && (
        <div className="mt-3 rounded-xl bg-stone-50 px-3 py-2">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-stone-400">
            Diagnostics
          </p>
          <div className="mt-2 space-y-1">
            {round.diagnostics.slice(0, 3).map((diagnostic) => (
              <p key={diagnostic} className="line-clamp-2 text-xs leading-5 text-stone-600">
                {diagnostic}
              </p>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

type FileTreeNode = {
  name: string;
  path: string;
  type: 'file' | 'folder';
  children: FileTreeNode[];
};

function buildFileTree(files: string[]): FileTreeNode[] {
  const root: FileTreeNode[] = [];

  files.forEach((rawFile) => {
    const parts = rawFile.replace(/\\/g, '/').split('/').filter(Boolean);
    let currentLevel = root;
    let currentPath = '';

    parts.forEach((part, index) => {
      currentPath = currentPath ? `${currentPath}/${part}` : part;
      const isFile = index === parts.length - 1;
      let node = currentLevel.find((item) => item.name === part && item.type === (isFile ? 'file' : 'folder'));

      if (!node) {
        node = {
          name: part,
          path: currentPath,
          type: isFile ? 'file' : 'folder',
          children: [],
        };
        currentLevel.push(node);
      }

      currentLevel = node.children;
    });
  });

  const sortTree = (nodes: FileTreeNode[]) => {
    nodes.sort((a, b) => {
      if (a.type !== b.type) return a.type === 'folder' ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
    nodes.forEach((node) => sortTree(node.children));
  };

  sortTree(root);
  return root;
}

function getNodeStatus(node: FileTreeNode, completedFiles: Set<string>): 'done' | 'partial' | 'pending' {
  if (node.type === 'file') {
    return completedFiles.has(node.path) ? 'done' : 'pending';
  }

  if (!node.children.length) return 'pending';
  const childStatuses = node.children.map((child) => getNodeStatus(child, completedFiles));
  if (childStatuses.every((status) => status === 'done')) return 'done';
  if (childStatuses.some((status) => status === 'done' || status === 'partial')) return 'partial';
  return 'pending';
}

function FileTreeRows({
  nodes,
  completedFiles,
  depth = 0,
}: {
  nodes: FileTreeNode[];
  completedFiles: Set<string>;
  depth?: number;
}) {
  return (
    <>
      {nodes.map((node) => {
        const status = getNodeStatus(node, completedFiles);
        const Icon = node.type === 'folder' ? FolderOpen : File;
        return (
          <div key={`${node.type}-${node.path}`}>
            <div
              className={`group flex items-center gap-2 rounded-xl px-2.5 py-1.5 text-xs transition ${
                status === 'done'
                  ? 'bg-emerald-400/10 text-emerald-200 ring-1 ring-emerald-400/20'
                  : status === 'partial'
                    ? 'bg-cyan-400/10 text-cyan-100 ring-1 ring-cyan-400/15'
                    : 'text-slate-500 hover:bg-white/5 hover:text-slate-300'
              }`}
              style={{ paddingLeft: `${depth * 14 + 10}px` }}
              title={node.path}
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  status === 'done'
                    ? 'bg-emerald-300 shadow-[0_0_10px_rgba(110,231,183,0.95)]'
                    : status === 'partial'
                      ? 'bg-cyan-300 shadow-[0_0_10px_rgba(103,232,249,0.75)]'
                      : 'bg-slate-700'
                }`}
              />
              <Icon className={`h-3.5 w-3.5 ${status === 'pending' ? 'text-slate-600' : ''}`} />
              <span className="truncate">{node.name}</span>
            </div>
            {node.children.length > 0 && (
              <FileTreeRows nodes={node.children} completedFiles={completedFiles} depth={depth + 1} />
            )}
          </div>
        );
      })}
    </>
  );
}

function FileTreeProgress({
  allFiles,
  completedFiles,
  fallbackFiles,
}: {
  allFiles: string[];
  completedFiles: string[];
  fallbackFiles: string[];
}) {
  const files = allFiles.length ? allFiles : fallbackFiles;
  const completedSet = useMemo(() => new Set(completedFiles), [completedFiles]);
  const tree = useMemo(() => buildFileTree(files), [files]);

  if (!files.length) {
    return <p className="text-sm text-slate-500">Files appear after the implementation plan is parsed.</p>;
  }

  return (
    <div className="max-h-80 overflow-y-auto pr-1">
      <FileTreeRows nodes={tree} completedFiles={completedSet} />
    </div>
  );
}

export default function PaperToCodePage() {
  const [inputMethod, setInputMethod] = useState<InputMethod>('file');
  const [uploadedFilePath, setUploadedFilePath] = useState<string | null>(null);
  const [uploadedPaperFiles, setUploadedPaperFiles] = useState<UploadedPaperFile[]>([]);
  const [planningImageFiles, setPlanningImageFiles] = useState<PlanningImageFile[]>([]);
  const [supplementaryRequirements, setSupplementaryRequirements] = useState('');
  const [workflowMode, setWorkflowMode] = useState<WorkflowMode>('raw_fast');
  const [showCancelDialog, setShowCancelDialog] = useState(false);
  const [isCancelling, setIsCancelling] = useState(false);
  const [visibleRole, setVisibleRole] = useState<'idle' | 'planner' | 'executor' | 'memory'>('idle');
  const [batchQueue, setBatchQueue] = useState<BatchQueueItem[]>([]);
  const [activeBatchIndex, setActiveBatchIndex] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const {
    activeTaskId,
    status,
    message,
    startedAt,
    completedAt,
    steps,
    generatedFiles,
    activityLogs,
    result,
    error,
    roundTraces,
    agentState,
    fileProgress,
    currentStage,
    artifacts,
    setActiveTask,
    setSteps,
    setStatus,
    reset,
  } = useWorkflowStore();

  useStreaming(activeTaskId);

  const selectedMode = WORKFLOW_MODE_OPTIONS.find((mode) => mode.value === workflowMode) || WORKFLOW_MODE_OPTIONS[0];
  const isRunning = status === 'running';
  const isDeepPlan = workflowMode.includes('deepplan');
  const isIndexed = workflowMode.includes('infer');
  const activeStage = STAGE_META[currentStage] || STAGE_META.idle;
  const StageIcon = activeStage.icon;
  const filePercent = fileProgress.total > 0
    ? Math.min(100, Math.round((fileProgress.implemented / fileProgress.total) * 100))
    : 0;

  const visibleRounds = useMemo(() => [...roundTraces].reverse(), [roundTraces]);
  const latestRound = visibleRounds[0];
  const implementedFiles = useMemo(
    () =>
      Array.from(
        new Set(roundTraces.flatMap((round) => round.completedFiles).filter(Boolean))
      ),
    [roundTraces]
  );
  const visibleGeneratedFiles = implementedFiles.length ? implementedFiles : generatedFiles;
  const runtimeSeconds = useMemo(() => {
    if (!startedAt) return 0;
    const end = completedAt || (status === 'running' ? now : null);
    if (!end) return 0;
    return Math.max(0, Math.floor((end - startedAt) / 1000));
  }, [startedAt, completedAt, status, now]);
  const runtimeLabel = useMemo(() => {
    const hours = Math.floor(runtimeSeconds / 3600);
    const minutes = Math.floor((runtimeSeconds % 3600) / 60);
    const seconds = runtimeSeconds % 60;
    if (hours > 0) return `${hours}h ${minutes}m`;
    if (minutes > 0) return `${minutes}m ${seconds}s`;
    return `${seconds}s`;
  }, [runtimeSeconds]);
  const completedRoundCount = roundTraces.filter((round) => round.status === 'completed').length;
  const referenceHintCount = roundTraces.reduce((total, round) => total + round.referenceSearches.length, 0);
  const repairCount = roundTraces.filter((round) => Boolean(round.repairFile)).length;
  const plannerActive = agentState.planner === 'active' || visibleRole === 'planner';
  const executorActive = agentState.executor === 'active' || visibleRole === 'executor';
  const memoryActive = agentState.memory === 'active' || visibleRole === 'memory';
  const artifactPath = useMemo(
    () =>
      findFirstStringByKey(result, [
        'code_directory',
        'generated_code_dir',
        'workspace_dir',
        'paper_dir',
        'final_report_path',
        'implementation_report_path',
      ]),
    [result]
  );
  const visibleArtifactPath =
    artifacts.finalReportPath ||
    artifacts.implementationReportPath ||
    artifacts.codeDirectory ||
    artifactPath;

  useEffect(() => {
    if (
      agentState.activeRole !== 'planner' &&
      agentState.activeRole !== 'executor' &&
      agentState.activeRole !== 'memory'
    ) {
      if (!isRunning) setVisibleRole('idle');
      return;
    }
    setVisibleRole(agentState.activeRole);
    const holdTimer = window.setTimeout(() => {
      setVisibleRole((current) => (current === agentState.activeRole ? 'idle' : current));
    }, 1800);
    return () => window.clearTimeout(holdTimer);
  }, [agentState.activeRole, isRunning]);

  useEffect(() => {
    if (status !== 'running') {
      setNow(Date.now());
      return;
    }
    const timer = window.setInterval(() => {
      setNow(Date.now());
    }, 1000);
    return () => window.clearInterval(timer);
  }, [status, startedAt, completedAt]);

  useEffect(() => {
    if (status === 'completed' && result) {
      toast.success('DeepRepro run complete', 'The reproduction workspace is ready.');
    } else if (status === 'error' && error) {
      toast.error('Processing failed', error);
    }
  }, [status, error, result]);

  useEffect(() => {
    if (activeBatchIndex === null || batchQueue.length === 0) return;

    const currentItem = batchQueue[activeBatchIndex];
    if (!currentItem || currentItem.status !== 'running') return;
    if (status !== 'completed' && status !== 'error') return;

    setBatchQueue((current) =>
      current.map((item, index) => {
        if (index !== activeBatchIndex) return item;
        return {
          ...item,
          status: status === 'completed' ? 'completed' : 'error',
        };
      })
    );

    if (status === 'error') {
      toast.error('Batch stopped', `${currentItem.label} failed. Remaining papers were not started.`);
      return;
    }

    const nextIndex = activeBatchIndex + 1;
    const nextItem = batchQueue[nextIndex];
    if (!nextItem) {
      toast.success('Batch complete', 'All uploaded papers have been processed.');
      setActiveBatchIndex(null);
      return;
    }

    window.setTimeout(() => {
      reset();
      setSteps(PAPER_TO_CODE_STEPS);
      setBatchQueue((current) =>
        current.map((item, index) =>
          index === nextIndex ? { ...item, status: 'running' } : item
        )
      );
      setActiveBatchIndex(nextIndex);
      setActiveTask(nextItem.taskId, 'paper-to-code');
      toast.info('Starting next paper', nextItem.label);
    }, 250);
  }, [activeBatchIndex, batchQueue, reset, setActiveTask, setSteps, status]);

  const handleCancelTask = async () => {
    if (!activeTaskId) return;
    setIsCancelling(true);
    try {
      await workflowsApi.cancel(activeTaskId);
      setStatus('idle');
      setBatchQueue([]);
      setActiveBatchIndex(null);
      reset();
      toast.info('Task cancelled', 'The workflow has been stopped.');
    } catch {
      toast.error('Cancel failed', 'Could not cancel the task.');
    } finally {
      setIsCancelling(false);
      setShowCancelDialog(false);
    }
  };

  const handleStart = async (inputSource: string, inputType: 'file' | 'url') => {
    try {
      setBatchQueue([]);
      setActiveBatchIndex(null);
      reset();
      setSteps(PAPER_TO_CODE_STEPS);
      const response = await workflowsApi.startPaperToCode(
        inputSource,
        inputType,
        workflowMode.includes('infer'),
        workflowMode,
        supplementaryRequirements.trim(),
        planningImageFiles.map((file) => file.path)
      );
      setActiveTask(response.task_id, 'paper-to-code');
      toast.info('DeepRepro started', `${selectedMode.label} is now running.`);
      setTimeout(() => {
        document.getElementById('deeprepro-monitor')?.scrollIntoView({
          behavior: 'smooth',
          block: 'start',
        });
      }, 100);
    } catch {
      toast.error('Failed to start DeepRepro', 'Please check the input and try again.');
    }
  };

  const handleStartFile = async () => {
    if (uploadedPaperFiles.length <= 1) {
      const inputSource = uploadedPaperFiles[0]?.path || uploadedFilePath;
      if (inputSource) {
        await handleStart(inputSource, 'file');
      }
      return;
    }

    try {
      reset();
      setSteps(PAPER_TO_CODE_STEPS);
      const response = await workflowsApi.startPaperToCodeBatch(
        uploadedPaperFiles.map((file) => ({
          input_source: file.path,
          input_type: 'file',
          label: file.name,
        })),
        workflowMode.includes('infer'),
        workflowMode,
        supplementaryRequirements.trim(),
        planningImageFiles.map((file) => file.path)
      );

      const queue = [...response.tasks]
        .sort((left, right) => left.order - right.order)
        .map((task, index) => ({
          taskId: task.task_id,
          label: task.label || `Paper ${index + 1}`,
          status: index === 0 ? 'running' : 'queued',
        } satisfies BatchQueueItem));

      setBatchQueue(queue);
      setActiveBatchIndex(0);
      setStatus('running');
      if (queue[0]) {
        setActiveTask(queue[0].taskId, 'paper-to-code');
      }
      toast.info('DeepRepro batch started', `${queue.length} papers will run in upload order.`);
      setTimeout(() => {
        document.getElementById('deeprepro-monitor')?.scrollIntoView({
          behavior: 'smooth',
          block: 'start',
        });
      }, 100);
    } catch {
      toast.error('Failed to start batch', 'Please check the uploaded papers and try again.');
    }
  };

  const canStartFile = inputMethod === 'file' && uploadedPaperFiles.length > 0 && !isRunning;

  return (
    <div className="space-y-8 px-5 py-8 sm:px-8 lg:py-10">
      <section className="relative left-1/2 flex min-h-[calc(100vh-4.5rem)] w-screen -translate-x-1/2 overflow-hidden border-y border-cyan-200/10 bg-[#030817] px-6 py-10 text-cyan-50 shadow-2xl shadow-cyan-950/30 sm:px-10 lg:px-[7vw]">
        <motion.div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0"
          animate={{
            backgroundPosition: ['0% 0%', '100% 100%'],
          }}
          transition={{
            duration: 20,
            repeat: Infinity,
            repeatType: 'reverse',
            ease: 'linear',
          }}
          style={{
            backgroundImage:
              'radial-gradient(circle at 18% 18%, rgba(34,211,238,0.22), transparent 32%), radial-gradient(circle at 78% 26%, rgba(124,58,237,0.28), transparent 30%), linear-gradient(135deg, rgba(15,23,42,0.2), rgba(2,6,23,0.96))',
            backgroundSize: '120% 120%',
          }}
        />
        <div className="absolute inset-0 opacity-[0.16] [background-image:linear-gradient(rgba(125,211,252,0.35)_1px,transparent_1px),linear-gradient(90deg,rgba(125,211,252,0.35)_1px,transparent_1px)] [background-size:72px_72px]" />
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_72%_48%,rgba(34,211,238,0.2),transparent_30%),radial-gradient(circle_at_84%_18%,rgba(168,85,247,0.22),transparent_28%),linear-gradient(90deg,rgba(2,6,23,0.18),rgba(2,6,23,0.02)_55%,rgba(2,6,23,0.4))]" />
        <div className="absolute inset-x-0 bottom-0 h-44 bg-gradient-to-t from-[#020617] via-[#020617]/55 to-transparent" />
        <motion.div
          aria-hidden="true"
          className="absolute left-10 top-10 h-24 w-24 rounded-full border border-cyan-300/20"
          animate={{ rotate: 360, scale: [1, 1.05, 1] }}
          transition={{ duration: 18, repeat: Infinity, ease: 'linear' }}
        />
        <motion.div
          aria-hidden="true"
          className="absolute bottom-10 right-16 h-36 w-36 rounded-full border border-violet-300/20"
          animate={{ rotate: -360, scale: [1, 1.08, 1] }}
          transition={{ duration: 24, repeat: Infinity, ease: 'linear' }}
        />
        <motion.div
          aria-hidden="true"
          className="absolute left-1/2 top-1/2 h-[32rem] w-[32rem] -translate-x-1/2 -translate-y-1/2 rounded-full border border-cyan-300/10"
          animate={{ rotate: 360 }}
          transition={{ duration: 40, repeat: Infinity, ease: 'linear' }}
        />
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          className="relative z-10 flex w-full flex-col gap-10 self-center"
        >
          <div className="grid w-full gap-10 lg:grid-cols-[0.95fr_1.05fr] lg:items-start">
            <div className="relative pt-3">
              <div className="mb-5 flex w-fit items-center gap-2 rounded-full border border-cyan-200/20 bg-cyan-100/10 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.22em] text-cyan-100 shadow-lg shadow-cyan-950/20">
                <Sparkles className="h-3.5 w-3.5" />
                Neural Reproduction Harness
              </div>
              <div className="flex items-start gap-5">
                <img
                  src="/deeprepro_logo.png"
                  alt="DeepRepro logo"
                  className="mt-2 h-16 w-16 shrink-0 rounded-2xl object-contain shadow-[0_0_30px_rgba(34,211,238,0.22)] sm:h-20 sm:w-20"
                />
                <h1 className="font-serif text-5xl font-semibold leading-[0.96] tracking-tight text-white sm:text-6xl xl:text-8xl">
                  Welcome to DeepRepro
                </h1>
              </div>
              <p className="mt-5 max-w-3xl text-lg leading-8 text-cyan-100/82 sm:text-xl">
                DeepRepro: Automatic ML Paper-to-Code Reproduction Framework
              </p>
              <div className="mt-10 flex flex-col gap-4 sm:flex-row sm:items-center">
                <button
                  type="button"
                  onClick={() =>
                    document.getElementById('deeprepro-launch')?.scrollIntoView({
                      behavior: 'smooth',
                      block: 'start',
                    })
                  }
                  className="inline-flex h-16 items-center justify-center rounded-[1.5rem] bg-cyan-300 px-10 text-lg font-semibold text-slate-950 shadow-[0_0_52px_rgba(34,211,238,0.52)] transition hover:-translate-y-0.5 hover:bg-cyan-200 hover:shadow-[0_0_72px_rgba(34,211,238,0.68)]"
                >
                  Try now
                  <UploadCloud className="ml-3 h-5 w-5" />
                </button>
              </div>
              <p className="mt-5 max-w-2xl text-base leading-7 text-cyan-50/68">Upload a paper, optional figures, and reproduction requirements. Choose a mode and run the full paper-to-code workflow with one click.</p>
            </div>

            <div className="relative mx-auto -mt-2 w-full max-w-2xl lg:-mt-8 lg:max-w-3xl xl:-mt-10">
              <motion.div
                aria-hidden="true"
                className="absolute inset-[-3rem] rounded-full bg-cyan-400/22 blur-3xl"
                animate={{ opacity: [0.35, 0.75, 0.35], scale: [0.96, 1.06, 0.96] }}
                transition={{ duration: 7, repeat: Infinity, ease: 'easeInOut' }}
              />
              <div className="absolute inset-[-2rem] rounded-full bg-violet-500/16 blur-3xl" />
              <div className="absolute inset-[-2.5rem] bg-[radial-gradient(circle_at_50%_48%,rgba(34,211,238,0.16),transparent_48%),radial-gradient(circle_at_63%_30%,rgba(168,85,247,0.16),transparent_42%)]" />
              <div className="relative">
                <img
                  src="/deep_repro.png"
                  alt="DeepRepro multi-agent paper-to-code workflow"
                  className="relative z-0 aspect-square w-full object-contain drop-shadow-[0_0_64px_rgba(34,211,238,0.35)] saturate-125"
                />
              </div>
            </div>
          </div>

          <div className="grid w-full gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {DEEPREPRO_FEATURES.map((feature, index) => {
              const Icon = feature.icon;
              return (
                <motion.div
                  key={feature.title}
                  initial={{ opacity: 0, y: 14 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.08 * (index + 1) }}
                  className="group min-h-[10rem] rounded-3xl border border-cyan-200/12 bg-slate-950/35 p-5 shadow-[inset_0_1px_0_rgba(255,255,255,0.06)] backdrop-blur transition hover:border-cyan-200/30 hover:bg-cyan-100/8"
                >
                  <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-2xl bg-cyan-300 text-slate-950 shadow-[0_0_22px_rgba(34,211,238,0.35)] transition group-hover:shadow-[0_0_32px_rgba(34,211,238,0.6)]">
                    <Icon className="h-5 w-5" />
                  </div>
                  <p className="text-base font-semibold text-white">{feature.title}</p>
                  <p className="mt-2 text-sm leading-6 text-cyan-50/58">{feature.description}</p>
                </motion.div>
              );
            })}
          </div>
        </motion.div>

      </section>

      <section
        id="deeprepro-launch"
        className="scroll-mt-6 rounded-[2.5rem] border border-stone-200 bg-white/70 p-6 shadow-xl shadow-stone-900/5 backdrop-blur"
      >
        <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="text-sm font-semibold uppercase tracking-[0.2em] text-stone-400">
              Launch
            </p>
            <h2 className="mt-1 text-3xl font-semibold text-stone-950">Start a reproduction run</h2>
            <p className="mt-2 text-sm text-stone-500">
              Paper is required. Figures and user intent are optional planning context.
            </p>
          </div>
          <span className={`w-fit rounded-full px-3 py-1 text-xs font-semibold ring-1 ${statusPill(status)}`}>
            {status}
          </span>
        </div>

        <div className="space-y-5">
          <div className="rounded-[2rem] border border-stone-200 bg-stone-50/80 p-5">
            <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <div className="flex items-center gap-2 text-sm font-semibold text-stone-900">
                  <FileText className="h-4 w-4 text-amber-600" />
                  Research paper
                </div>
                <p className="mt-1 text-sm text-stone-500">
                  Upload a paper file or provide a supported URL.
                </p>
              </div>
              <div className="grid grid-cols-2 gap-1 rounded-2xl bg-stone-100 p-1">
                {(['file', 'url'] as InputMethod[]).map((method) => (
                  <button
                    key={method}
                    disabled={isRunning}
                    onClick={() => setInputMethod(method)}
                    className={`rounded-xl px-4 py-2 text-sm font-semibold transition ${
                      inputMethod === method
                        ? 'bg-white text-stone-950 shadow-sm'
                        : 'text-stone-500 hover:text-stone-900'
                    }`}
                  >
                    {method === 'file' ? 'Upload PDF' : 'Paper URL'}
                  </button>
                ))}
              </div>
            </div>

            {inputMethod === 'file' ? (
              <FileUploader
                multiple
                onFileUploaded={(fileId, path, fileName) => {
                  setUploadedPaperFiles((current) =>
                    current.some((file) => file.fileId === fileId)
                      ? current
                      : [...current, { fileId, path, name: fileName || 'Untitled paper' }]
                  );
                  setUploadedFilePath(path);
                }}
                onFileRemoved={(fileId) =>
                  setUploadedPaperFiles((current) => {
                    const next = current.filter((file) => file.fileId !== fileId);
                    setUploadedFilePath(next[0]?.path ?? null);
                    return next;
                  })
                }
                disabled={isRunning}
                title="Drop the research paper"
                description="PDF, Markdown, or text up to 100MB"
              />
            ) : (
              <UrlInput
                onSubmit={(url) => handleStart(url, 'url')}
                isLoading={isRunning}
                disabled={isRunning}
              />
            )}
          </div>

          <div className="grid gap-5 lg:grid-cols-2">
            <ImageContextCard
              imageCount={planningImageFiles.length}
              onImageUploaded={(fileId, path) =>
                setPlanningImageFiles((current) =>
                  current.some((file) => file.fileId === fileId)
                    ? current
                    : [...current, { fileId, path }]
                )
              }
              onImageRemoved={(fileId) =>
                setPlanningImageFiles((current) => current.filter((file) => file.fileId !== fileId))
              }
              disabled={isRunning}
            />
            <SupplementaryContext
              value={supplementaryRequirements}
              onChange={setSupplementaryRequirements}
              disabled={isRunning}
            />
          </div>

          <div className="rounded-[2rem] border border-stone-200 bg-stone-50/80 p-5">
            <div className="mb-4 flex items-center gap-2">
              <RefreshCw className="h-4 w-4 text-stone-500" />
              <p className="text-sm font-semibold text-stone-900">Execution mode</p>
            </div>
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
              {WORKFLOW_MODE_OPTIONS.map((option) => {
                const Icon = option.icon;
                const selected = workflowMode === option.value;
                return (
                  <button
                    key={option.value}
                    type="button"
                    disabled={isRunning}
                    onClick={() => setWorkflowMode(option.value)}
                    className={`rounded-2xl border p-4 text-left transition ${
                      selected
                        ? 'border-stone-900 bg-white shadow-md shadow-stone-900/8'
                        : 'border-stone-200 bg-white/70 hover:border-stone-300'
                    } disabled:cursor-not-allowed disabled:opacity-60`}
                  >
                    <div className="flex items-center justify-between gap-3">
                      <Icon className={`h-5 w-5 ${selected ? 'text-stone-900' : 'text-stone-400'}`} />
                      <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold text-amber-800">
                        {option.badge}
                      </span>
                    </div>
                    <p className="mt-3 text-sm font-semibold text-stone-950">{option.label}</p>
                    <p className="text-xs text-stone-400">{option.short}</p>
                    <p className="mt-2 text-xs leading-5 text-stone-500">{option.description}</p>
                  </button>
                );
              })}
            </div>
          </div>

          {inputMethod === 'file' && batchQueue.length > 0 && (
            <BatchQueuePanel items={batchQueue} />
          )}

          <div className="flex flex-col gap-3 sm:flex-row">
            {inputMethod === 'file' && (
              <Button
                onClick={handleStartFile}
                disabled={!canStartFile}
                isLoading={isRunning}
                className="h-12 flex-1 rounded-2xl bg-stone-950 hover:bg-stone-800"
              >
                <UploadCloud className="mr-2 h-4 w-4" />
                Start DeepRepro
              </Button>
            )}
            {isRunning && (
              <button
                onClick={() => setShowCancelDialog(true)}
                disabled={isCancelling}
                className="inline-flex h-12 items-center justify-center rounded-2xl border border-red-200 bg-red-50 px-4 text-sm font-semibold text-red-700 transition hover:bg-red-100 disabled:opacity-60"
              >
                <StopCircle className="mr-2 h-4 w-4" />
                Stop
              </button>
            )}
          </div>
        </div>
      </section>

      <section id="deeprepro-monitor" className="scroll-mt-6 space-y-6">
        <div className="relative overflow-hidden rounded-[2.25rem] border border-stone-900/10 bg-stone-950 p-6 text-amber-50 shadow-xl">
          <motion.div
            aria-hidden="true"
            className="absolute right-[-5rem] top-[-5rem] h-52 w-52 rounded-full bg-emerald-400/20 blur-3xl"
            animate={{ opacity: [0.45, 0.8, 0.45], scale: [1, 1.08, 1] }}
            transition={{ duration: 9, repeat: Infinity, ease: 'easeInOut' }}
          />
          <div className="relative grid gap-5 lg:grid-cols-[1.2fr_0.8fr] lg:items-center">
            <div>
              <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-amber-100/15 bg-white/8 px-3 py-1 text-xs font-semibold text-amber-100">
                <StageIcon className="h-3.5 w-3.5" />
                Execution monitor
              </div>
              <h2 className="text-3xl font-semibold tracking-tight">DeepRepro is operating in {selectedMode.label}</h2>
              <p className="mt-3 max-w-3xl text-sm leading-7 text-amber-50/65">
                {activeStage.title}: {activeStage.description}
                {message ? ` - ${message}` : ''}
              </p>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="rounded-3xl border border-white/10 bg-white/8 p-4">
                <p className="text-xs uppercase tracking-[0.18em] text-amber-50/45">Runtime</p>
                <p className="mt-2 text-3xl font-semibold">{runtimeLabel}</p>
              </div>
              <div className="rounded-3xl border border-white/10 bg-white/8 p-4">
                <p className="text-xs uppercase tracking-[0.18em] text-amber-50/45">Files</p>
                <p className="mt-2 text-3xl font-semibold">
                  {fileProgress.total > 0 ? `${fileProgress.implemented}/${fileProgress.total}` : 'N/A'}
                </p>
              </div>
            </div>
          </div>
        </div>

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <MetricCard
            label="Completed rounds"
            value={completedRoundCount}
            detail={latestRound ? `Latest visible round: ${latestRound.roundId}` : 'Round traces appear during implementation.'}
          />
          <MetricCard
            label="Repair rounds"
            value={repairCount}
            detail="DeepPlan can schedule targeted repair before new files."
          />
          <MetricCard
            label="Reference hints"
            value={referenceHintCount}
            detail={isIndexed ? 'Index-guided modes expose optional reference hints.' : 'Reference index is disabled in this mode.'}
          />
          <MetricCard
            label="Generated files"
            value={visibleGeneratedFiles.length}
            detail="Files streamed from backend write operations."
          />
        </div>

        <ModeFlow isDeepPlan={isDeepPlan} isIndexed={isIndexed} />

        <div className="grid gap-6 xl:grid-cols-[0.9fr_1.1fr]">
          <div className="space-y-6">
            <div className="rounded-[2rem] border border-stone-200 bg-white/75 p-5 shadow-sm">
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <p className="text-sm font-semibold text-stone-950">Control surface</p>
                  <p className="text-xs text-stone-500">Stage, subplan agent, and execute agent flow.</p>
                </div>
                <span className={`rounded-full px-3 py-1 text-xs font-semibold ring-1 ${statusPill(status)}`}>
                  {isRunning ? 'Live' : status}
                </span>
              </div>
              <StageTimeline steps={steps} activeStageTitle={activeStage.title} />
            </div>

            <div className="space-y-3">
              <div className="grid gap-3 lg:grid-cols-2">
                <AgentLamp
                  title="Subplan Agent"
                  active={plannerActive}
                  muted={!isDeepPlan}
                  icon={Bot}
                  tone="violet"
                  subtitle={isDeepPlan ? 'Subplans, repairs, file order' : 'Fast mode uses next-step summaries; no DeepPlan subplan turn.'}
                />
                <AgentLamp
                  title="Execute Agent"
                  active={executorActive}
                  icon={Code2}
                  tone="cyan"
                  subtitle={agentState.message || 'Writes files, applies repairs, and searches references when useful.'}
                />
              </div>
              <MemoryBrainCard
                active={memoryActive}
                subtitle={agentState.memoryMessage || 'Async file summaries run after writes and finish before the next round.'}
              />
            </div>

            <div className="rounded-[2rem] border border-stone-200 bg-white/70 p-5">
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <p className="text-sm font-semibold text-stone-950">File progress</p>
                  <p className="text-xs text-stone-500">
                    {fileProgress.total > 0
                      ? `${fileProgress.implemented}/${fileProgress.total} implemented / ${fileProgress.remaining} remaining`
                      : 'File-level progress appears during implementation.'}
                  </p>
                </div>
                <span className="text-2xl font-semibold text-stone-950">{filePercent}%</span>
              </div>
              <div className="h-3 overflow-hidden rounded-full bg-stone-100">
                <motion.div
                  className="h-full rounded-full bg-emerald-500"
                  animate={{ width: `${filePercent}%` }}
                />
              </div>
              {fileProgress.currentFile && (
                <p className="mt-3 truncate rounded-xl bg-stone-100 px-3 py-2 text-xs text-stone-600">
                  Current: {fileProgress.currentFile}
                </p>
              )}
            </div>
          </div>

          <div className="space-y-6">
            <div className="rounded-[2rem] border border-stone-200 bg-white/70 p-5 shadow-sm backdrop-blur">
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <p className="text-sm font-semibold text-stone-950">Round trace</p>
                  <p className="text-xs text-stone-500">Subplans, repairs, reference hints, and completed files.</p>
                </div>
                {isIndexed && (
                  <span className="rounded-full bg-amber-100 px-3 py-1 text-xs font-semibold text-amber-800">
                    Reference enabled
                  </span>
                )}
              </div>
              {visibleRounds.length ? (
                <div className="max-h-[34rem] space-y-3 overflow-y-auto overscroll-contain pr-2">
                  {visibleRounds.map((round) => (
                    <RoundCard key={round.roundId} round={round} />
                  ))}
                </div>
              ) : (
                <div className="flex min-h-[240px] items-center justify-center rounded-2xl border border-dashed border-stone-300 bg-stone-50/70 text-center">
                  <div>
                    <Bot className="mx-auto h-10 w-10 text-stone-300" />
                    <p className="mt-3 text-sm font-medium text-stone-600">Round details will appear during implementation.</p>
                    <p className="mt-1 text-xs text-stone-400">DeepPlan exposes richer subplans; fast mode shows batch traces.</p>
                  </div>
                </div>
              )}
            </div>

            <div className="grid gap-6 lg:grid-cols-[0.9fr_1.1fr]">
              <div className="rounded-[2rem] border border-stone-200 bg-stone-950 p-5 text-amber-50">
                <div className="mb-4 flex items-center justify-between">
                  <div>
                    <p className="text-sm font-semibold">Live event feed</p>
                    <p className="mt-1 text-xs text-amber-50/45">Raw workflow messages remain available for debugging.</p>
                  </div>
                  {isRunning && <Loader2 className="h-4 w-4 animate-spin text-emerald-400" />}
                </div>
                <div className="max-h-80 space-y-3 overflow-y-auto pr-1">
                  {activityLogs.length ? (
                    activityLogs.slice(-24).reverse().map((log) => (
                      <div key={log.id} className="rounded-2xl bg-white/7 px-3 py-2">
                        <p className="text-xs text-amber-50/45">
                          {new Date(log.timestamp).toLocaleTimeString()}
                        </p>
                        <p className="mt-1 text-sm leading-5 text-amber-50/80">{log.message}</p>
                      </div>
                    ))
                  ) : (
                    <p className="text-sm text-amber-50/45">No events yet.</p>
                  )}
                </div>
              </div>

              <div className="rounded-[2rem] border border-stone-200 bg-white/70 p-5">
                <div className="mb-4 flex items-center justify-between">
                  <div>
                    <p className="text-sm font-semibold text-stone-950">Artifact panel</p>
                    <p className="mt-1 text-xs text-stone-500">Full planned file tree with completed nodes lit up.</p>
                  </div>
                  <span className="rounded-full bg-stone-100 px-2.5 py-1 text-xs text-stone-500">
                    {fileProgress.total || visibleGeneratedFiles.length}
                  </span>
                </div>
                {visibleArtifactPath && (
                  <div className="mb-3 rounded-2xl border border-emerald-200 bg-emerald-50 px-3 py-2">
                    <p className="text-xs font-semibold uppercase tracking-[0.16em] text-emerald-700">
                      Result path
                    </p>
                    <p className="mt-1 break-all text-xs leading-5 text-emerald-800">{visibleArtifactPath}</p>
                  </div>
                )}
                <div className="rounded-3xl border border-slate-800/70 bg-slate-950 p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.06)]">
                  <FileTreeProgress
                    allFiles={fileProgress.allFiles}
                    completedFiles={implementedFiles}
                    fallbackFiles={visibleGeneratedFiles}
                  />
                </div>
              </div>
            </div>

            {status === 'completed' && result && (
              <div className="rounded-[2rem] border border-emerald-200 bg-emerald-50 p-5">
                <div className="flex items-start gap-3">
                  <CheckCircle className="mt-0.5 h-6 w-6 text-emerald-600" />
                  <div>
                    <p className="font-semibold text-emerald-950">DeepRepro completed the reproduction run.</p>
                    <p className="mt-1 text-sm text-emerald-700">
                      Final reports and generated code are available in the workspace.
                    </p>
                  </div>
                </div>
              </div>
            )}

            {status === 'error' && error && (
              <div className="rounded-[2rem] border border-red-200 bg-red-50 p-5">
                <div className="flex items-start gap-3">
                  <AlertCircle className="mt-0.5 h-6 w-6 text-red-600" />
                  <div>
                    <p className="font-semibold text-red-950">DeepRepro failed.</p>
                    <p className="mt-1 text-sm text-red-700">{error}</p>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </section>

      <ConfirmDialog
        isOpen={showCancelDialog}
        title="Stop DeepRepro run?"
        message="The backend task will be cancelled. Generated partial artifacts may remain in the workspace."
        confirmLabel="Stop run"
        cancelLabel="Keep running"
        variant="danger"
        onConfirm={handleCancelTask}
        onCancel={() => setShowCancelDialog(false)}
      />
    </div>
  );
}

