import { useEffect, useState } from 'react';

export function RunArtifacts({ taskId }: { taskId: string }) {
  const [files, setFiles] = useState<string[]>([]);
  const [selected, setSelected] = useState('');
  const [content, setContent] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    let disposed = false;
    setFiles([]); setSelected(''); setContent(''); setError('');
    fetch(`/api/v1/artifacts/${taskId}`).then(async response => {
      if (!response.ok) throw new Error('Artifacts are not available for this run.');
      const data = await response.json();
      if (!disposed) setFiles(data.files);
    }).catch(error => { if (!disposed) setError(String(error.message)); });
    return () => { disposed = true; };
  }, [taskId]);
  useEffect(() => {
    if (!selected) return;
    let disposed = false;
    setContent('Loading…');
    fetch(`/api/v1/artifacts/${taskId}/file?path=${encodeURIComponent(selected)}`).then(async response => {
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Preview unavailable');
      if (!disposed) setContent(data.content);
    }).catch(error => { if (!disposed) setContent(String(error.message)); });
    return () => { disposed = true; };
  }, [taskId, selected]);
  return <div className="mt-4 space-y-3">
    <div className="flex flex-wrap items-center gap-3">
      <a className="rounded-xl bg-emerald-800 px-4 py-2 text-sm font-medium text-white" href={`/api/v1/artifacts/${taskId}/download`}>Download generated code</a>
      <select aria-label="Preview generated file" value={selected} onChange={e => setSelected(e.target.value)} className="max-w-full rounded-xl border border-emerald-200 bg-white px-3 py-2 text-sm text-stone-800">
        <option value="">Preview a generated file…</option>
        {files.map(path => <option key={path} value={path}>{path}</option>)}
      </select>
    </div>
    {error && <p className="text-sm text-amber-800">{error}</p>}
    {selected && <pre className="max-h-96 overflow-auto rounded-xl bg-stone-950 p-4 text-xs leading-5 text-stone-100">{content}</pre>}
  </div>;
}
