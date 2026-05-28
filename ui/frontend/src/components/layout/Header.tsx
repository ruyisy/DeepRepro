import { Link } from 'react-router-dom';
import { Loader2, Settings } from 'lucide-react';
import { useWorkflowStore } from '../../stores/workflowStore';

export default function Header() {
  const { status } = useWorkflowStore();
  const isRunning = status === 'running';

  return (
    <header className="sticky top-0 z-50 border-b border-cyan-100/10 bg-slate-950/55 text-cyan-50 backdrop-blur-2xl">
      <div className="flex h-18 w-full items-center justify-between px-4 py-3 sm:px-8 lg:px-12">
        <Link to="/" className="group flex items-center gap-3">
          <div className="relative">
            <img
              src="/deeprepro_logo.png"
              alt="DeepRepro"
              className="h-11 w-11 rounded-2xl object-cover shadow-lg shadow-cyan-950/30 ring-1 ring-cyan-100/20"
            />
            <span className="absolute -right-1 -top-1 h-3 w-3 rounded-full bg-emerald-400 ring-2 ring-slate-950" />
          </div>
          <div className="font-serif text-2xl font-semibold tracking-tight text-white">
            DeepRepro
          </div>
        </Link>

        <div className="ml-auto flex items-center gap-3">
          {isRunning && (
            <div className="hidden items-center gap-2 rounded-full border border-emerald-300 bg-emerald-50 px-3 py-1.5 text-sm font-medium text-emerald-800 sm:flex">
              <Loader2 className="h-4 w-4 animate-spin" />
              <span>Running</span>
            </div>
          )}
          <Link
            to="/settings"
            className="rounded-full border border-cyan-100/15 bg-white/8 p-2 text-cyan-50/75 transition hover:border-cyan-100/30 hover:bg-white/14 hover:text-white"
            title="Settings"
          >
            <Settings className="h-5 w-5" />
          </Link>
        </div>
      </div>
    </header>
  );
}

