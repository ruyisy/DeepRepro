import { useEffect, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Cpu, KeyRound, Link2, Save, Server, ShieldCheck } from 'lucide-react';
import { Button } from '../components/common';
import { toast } from '../components/common/Toaster';
import { configApi } from '../services/api';

const PROVIDER_INFO: Record<string, { name: string; description: string; accent: string }> = {
  openai: {
    name: 'OpenAI-compatible',
    description: 'OpenAI, DashScope compatible-mode, OpenRouter, and other compatible APIs.',
    accent: 'from-cyan-400 to-blue-500',
  },
  google: {
    name: 'Google Gemini',
    description: 'Gemini models for planning, analysis, and implementation.',
    accent: 'from-emerald-400 to-cyan-500',
  },
  anthropic: {
    name: 'Anthropic Claude',
    description: 'Claude models for careful planning and long-context reasoning.',
    accent: 'from-violet-400 to-fuchsia-500',
  },
};

type SettingsForm = {
  provider: string;
  defaultModel: string;
  planningModel: string;
  subplanModel: string;
  implementationModel: string;
  baseUrl: string;
  apiKey: string;
};

const EMPTY_FORM: SettingsForm = {
  provider: 'openai',
  defaultModel: '',
  planningModel: '',
  subplanModel: '',
  implementationModel: '',
  baseUrl: '',
  apiKey: '',
};

export default function SettingsPage() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<SettingsForm>(EMPTY_FORM);

  const { data: settings, isLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: configApi.getSettings,
  });

  const updateMutation = useMutation({
    mutationFn: configApi.updateLLMConfig,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
      queryClient.invalidateQueries({ queryKey: ['llm-providers'] });
      toast.success('Settings saved', 'DeepRepro LLM configuration was updated.');
    },
    onError: (error) => {
      toast.error('Failed to save settings', error instanceof Error ? error.message : 'Please try again.');
    },
  });

  const providers = useMemo(
    () => settings?.providers?.length ? settings.providers : ['openai', 'google', 'anthropic'],
    [settings]
  );

  useEffect(() => {
    if (!settings) return;
    const provider = settings.llm_provider || 'openai';
    setForm({
      provider,
      defaultModel: settings.models?.default || '',
      planningModel: settings.models?.planning || '',
      subplanModel: settings.models?.subplan || '',
      implementationModel: settings.models?.implementation || '',
      baseUrl: settings.api_base_urls?.[provider] || '',
      apiKey: '',
    });
  }, [settings]);

  const selectProvider = (provider: string) => {
    setForm((current) => ({
      ...current,
      provider,
      baseUrl: settings?.api_base_urls?.[provider] || '',
      apiKey: '',
    }));
  };

  const updateField = (field: keyof SettingsForm, value: string) => {
    setForm((current) => ({ ...current, [field]: value }));
  };

  const handleSave = () => {
    updateMutation.mutate({
      provider: form.provider,
      default_model: form.defaultModel,
      planning_model: form.planningModel,
      subplan_model: form.subplanModel,
      implementation_model: form.implementationModel,
      base_url: form.baseUrl,
      api_key: form.apiKey,
    });
  };

  if (isLoading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center text-cyan-50">
        <div className="h-10 w-10 animate-spin rounded-full border-2 border-cyan-300 border-t-transparent" />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6 px-5 py-8 sm:px-8">
      <motion.section
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        className="relative overflow-hidden rounded-[2.5rem] border border-cyan-200/15 bg-slate-950 p-8 text-cyan-50 shadow-2xl shadow-cyan-950/25"
      >
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_20%_20%,rgba(34,211,238,0.22),transparent_28%),radial-gradient(circle_at_80%_10%,rgba(124,58,237,0.24),transparent_30%)]" />
        <div className="relative flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-cyan-200/15 bg-white/8 px-3 py-1 text-xs font-semibold text-cyan-100">
              <ShieldCheck className="h-3.5 w-3.5" />
              Runtime configuration
            </div>
            <h1 className="font-serif text-4xl font-semibold tracking-tight sm:text-5xl">
              DeepRepro Settings
            </h1>
            <p className="mt-4 max-w-2xl text-sm leading-7 text-cyan-50/65">
              Select an LLM provider and configure model names, compatible API address, and API key.
              Changes are written to local configuration files and used by future runs.
            </p>
          </div>
          <div className="rounded-3xl border border-white/10 bg-white/8 p-4 backdrop-blur">
            <p className="text-xs uppercase tracking-[0.18em] text-cyan-50/45">Active provider</p>
            <p className="mt-2 text-2xl font-semibold">{PROVIDER_INFO[form.provider]?.name || form.provider}</p>
          </div>
        </div>
      </motion.section>

      <div className="grid gap-6 lg:grid-cols-[0.95fr_1.05fr]">
        <section className="rounded-[2rem] border border-stone-200 bg-white/80 p-5 shadow-sm backdrop-blur">
          <div className="mb-5 flex items-center gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-stone-950 text-cyan-200">
              <Server className="h-5 w-5" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-stone-950">Provider</h2>
              <p className="text-sm text-stone-500">Choose the backend used for future DeepRepro tasks.</p>
            </div>
          </div>

          <div className="space-y-3">
            {providers.map((provider) => {
              const info = PROVIDER_INFO[provider] || {
                name: provider,
                description: 'Custom provider',
                accent: 'from-stone-400 to-stone-700',
              };
              const selected = form.provider === provider;
              return (
                <button
                  key={provider}
                  type="button"
                  onClick={() => selectProvider(provider)}
                  className={`w-full rounded-3xl border p-4 text-left transition ${
                    selected
                      ? 'border-stone-950 bg-stone-950 text-white shadow-xl shadow-stone-900/15'
                      : 'border-stone-200 bg-white hover:border-stone-300'
                  }`}
                >
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex items-start gap-3">
                      <div className={`h-11 w-11 rounded-2xl bg-gradient-to-br ${info.accent}`} />
                      <div>
                        <p className={`font-semibold ${selected ? 'text-white' : 'text-stone-950'}`}>
                          {info.name}
                        </p>
                        <p className={`mt-1 text-sm leading-6 ${selected ? 'text-cyan-50/62' : 'text-stone-500'}`}>
                          {info.description}
                        </p>
                      </div>
                    </div>
                    {selected && <Check className="h-5 w-5 text-cyan-300" />}
                  </div>
                </button>
              );
            })}
          </div>
        </section>

        <section className="rounded-[2rem] border border-stone-200 bg-white/80 p-5 shadow-sm backdrop-blur">
          <div className="mb-5 flex items-center gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-cyan-100 text-cyan-700">
              <Cpu className="h-5 w-5" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-stone-950">Models and credentials</h2>
              <p className="text-sm text-stone-500">API keys are stored locally in `mcp_agent.secrets.yaml`.</p>
            </div>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <label className="space-y-2">
              <span className="text-xs font-semibold uppercase tracking-[0.16em] text-stone-400">Default model</span>
              <input
                value={form.defaultModel}
                onChange={(event) => updateField('defaultModel', event.target.value)}
                placeholder="qwen3.5-plus"
                className="input h-12 rounded-2xl"
              />
            </label>
            <label className="space-y-2">
              <span className="text-xs font-semibold uppercase tracking-[0.16em] text-stone-400">Planning model</span>
              <input
                value={form.planningModel}
                onChange={(event) => updateField('planningModel', event.target.value)}
                placeholder="qwen3.5-plus"
                className="input h-12 rounded-2xl"
              />
            </label>
            <label className="space-y-2">
              <span className="text-xs font-semibold uppercase tracking-[0.16em] text-stone-400">Sub-plan model</span>
              <input
                value={form.subplanModel}
                onChange={(event) => updateField('subplanModel', event.target.value)}
                placeholder="qwen3.5-plus"
                className="input h-12 rounded-2xl"
              />
            </label>
            <label className="space-y-2 sm:col-span-2">
              <span className="text-xs font-semibold uppercase tracking-[0.16em] text-stone-400">Execute model</span>
              <input
                value={form.implementationModel}
                onChange={(event) => updateField('implementationModel', event.target.value)}
                placeholder="qwen3.5-flash"
                className="input h-12 rounded-2xl"
              />
            </label>
            <label className="space-y-2 sm:col-span-2">
              <span className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.16em] text-stone-400">
                <Link2 className="h-3.5 w-3.5" />
                API base URL
              </span>
              <input
                value={form.baseUrl}
                onChange={(event) => updateField('baseUrl', event.target.value)}
                placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1"
                className="input h-12 rounded-2xl"
              />
            </label>
            <label className="space-y-2 sm:col-span-2">
              <span className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.16em] text-stone-400">
                <KeyRound className="h-3.5 w-3.5" />
                API key
              </span>
              <input
                value={form.apiKey}
                onChange={(event) => updateField('apiKey', event.target.value)}
                placeholder="Leave blank to keep the current key"
                type="password"
                className="input h-12 rounded-2xl"
              />
            </label>
          </div>

          <div className="mt-6 flex flex-col gap-3 border-t border-stone-200 pt-5 sm:flex-row sm:items-center sm:justify-between">
            <div className="text-xs leading-5 text-stone-500">
              Blank API key clears the current provider key. Fill it when switching providers.
            </div>
            <Button
              onClick={handleSave}
              isLoading={updateMutation.isPending}
              className="h-12 rounded-2xl bg-stone-950 px-5 hover:bg-stone-800"
            >
              <Save className="mr-2 h-4 w-4" />
              Save settings
            </Button>
          </div>
        </section>
      </div>

    </div>
  );
}
