import { useEffect, useState } from 'react';
import { workflowsApi } from '../../services/api';
import { useWorkflowStore } from '../../stores/workflowStore';
import type { RunMetrics } from '../../types/api';

export function RunMetricsPanel({ taskId, status }: { taskId: string | null; status: string }) {
  const [metrics, setMetrics] = useState<RunMetrics | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [canResume, setCanResume] = useState(false);
  const [resuming, setResuming] = useState(false);
  const [resumeError, setResumeError] = useState('');
  useEffect(() => {
    setMetrics(null);
    if (!taskId) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        const snapshot = await workflowsApi.getStatus(taskId);
        if (disposed) return;
        setMetrics(snapshot.metrics?.elapsed_seconds !== undefined ? snapshot.metrics : null);
        setUnavailable(false);
        setCanResume(Boolean(snapshot.can_resume));
        // REST recovery also covers missed terminal websocket events.
        const store = useWorkflowStore.getState();
        if (store.activeTaskId === taskId) useWorkflowStore.setState({ runElapsedSeconds: snapshot.metrics?.elapsed_seconds ?? null });
        if (store.activeTaskId === taskId && ['completed', 'error', 'cancelled', 'paused'].includes(snapshot.status)) {
          if (snapshot.result && !store.result) store.setResult(snapshot.result);
          if (snapshot.error) store.setError(snapshot.error);
          if (store.status !== snapshot.status) store.setStatus(snapshot.status as 'completed' | 'error' | 'cancelled' | 'paused');
          if (snapshot.status === 'completed' || snapshot.can_resume) return;
        }
      } catch {
        if (!disposed) setUnavailable(true);
      }
      if (!disposed) timer = setTimeout(refresh, 2000);
    };
    void refresh();
    return () => { disposed = true; clearTimeout(timer); };
  }, [taskId, status]);
  if (!taskId) return null;
  const number = (value?: number) => value === undefined ? '—' : value.toLocaleString();
  return (
    <div className="rounded-[2rem] border border-stone-200 bg-white/80 p-5" aria-label="Run efficiency metrics">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-semibold text-stone-950">Run efficiency</p>
        <span className="text-xs text-stone-500">{metrics?.models.join(', ') || 'Waiting for API usage'}</span>
      </div>
      <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
        {[
          ['Active time', metrics ? `${Math.floor(metrics.elapsed_seconds / 60)}m ${Math.floor(metrics.elapsed_seconds % 60)}s` : '—'],
          ['Total tokens', number(metrics?.total_tokens)],
          ['LLM calls', number(metrics?.llm_calls)],
          ['Failed calls', number(metrics?.failed_calls)],
        ].map(([label, value]) => <div key={label}><p className="text-xs text-stone-500">{label}</p><p className="mt-1 text-xl font-semibold text-stone-950">{value}</p></div>)}
      </div>
      <p className="mt-3 text-xs text-stone-500">Input {number(metrics?.prompt_tokens)} · Output {number(metrics?.completion_tokens)} · Cached input {number(metrics?.cached_tokens)}</p>
      <p className="mt-1 text-xs text-stone-500">API-reported tokens · {number(metrics?.usage_reported_calls)} calls with usage · cancelled requests may have unreported usage</p>
      {['paused', 'error', 'cancelled'].includes(status) && <div className="mt-4 rounded-xl bg-amber-50 p-3 text-sm text-amber-950">
        <p>Progress is saved. Resume retries the unfinished step and reuses completed stages and files.</p>
        <button disabled={!canResume || resuming} className="mt-3 rounded-lg bg-stone-900 px-4 py-2 font-medium text-white disabled:opacity-50" onClick={async () => {
          setResuming(true); setResumeError('');
          try {
            await workflowsApi.resume(taskId);
            const store = useWorkflowStore.getState();
            store.setError(null); store.setResult(null); store.setStatus('running');
            store.addActivityLog('Resuming from saved progress', 0, 'info');
          } catch { setResumeError('Could not resume yet. Check the backend connection and try again.'); }
          finally { setResuming(false); }
        }}>{resuming ? 'Resuming…' : status === 'error' ? 'Retry from checkpoint' : 'Resume run'}</button>
        {resumeError && <p className="mt-2 text-red-700">{resumeError}</p>}
      </div>}
      {unavailable && <p className="mt-2 text-xs text-amber-700">Metrics connection unavailable; retrying.</p>}
      {metrics && <details className="mt-3 text-xs text-stone-600"><summary className="cursor-pointer">Stage timings</summary><div className="mt-2 grid grid-cols-2 gap-2">{Object.entries(metrics.stage_seconds).map(([stage, seconds]) => <span key={stage}>{stage.replace(/_/g, ' ')}: {seconds.toFixed(1)}s</span>)}</div></details>}
      <a className="mt-3 inline-block text-xs font-medium text-emerald-700 underline" href={`/api/v1/artifacts/${taskId}/metrics`}>Download metrics JSON</a>
    </div>
  );
}
