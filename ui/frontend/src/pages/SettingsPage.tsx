import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import { ArrowLeft, CheckCircle2, KeyRound, Save, Settings2 } from 'lucide-react';
import { Link } from 'react-router-dom';
import { Button } from '../components/common';
import { toast } from '../components/common/Toaster';
import { configApi } from '../services/api';
import type { SettingsResponse } from '../types/api';

const API_FORMATS: Record<string, string> = {
  openai: 'OpenAI-compatible',
  google: 'Google Gemini API',
  anthropic: 'Anthropic Messages API',
};
const DEFAULT_URLS: Record<string, string> = {
  openai: 'https://api.openai.com/v1',
  google: 'https://generativelanguage.googleapis.com',
  anthropic: 'https://api.anthropic.com',
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
  provider: 'openai', defaultModel: '', planningModel: '', subplanModel: '',
  implementationModel: '', baseUrl: '', apiKey: '',
};
function savedForm(settings: SettingsResponse, provider = settings.llm_provider): SettingsForm {
  const models = settings.provider_models?.[provider] || (provider === settings.llm_provider ? settings.models : {});
  return {
    provider, defaultModel: models.default || '', planningModel: models.planning || '',
    subplanModel: models.subplan || '', implementationModel: models.implementation || '',
    baseUrl: settings.api_base_urls?.[provider] || '', apiKey: '',
  };
}
function usesOneModel(form: SettingsForm) {
  return [form.planningModel, form.subplanModel, form.implementationModel].every(model => !model || model === form.defaultModel);
}
function payload(form: SettingsForm, oneModel: boolean) {
  const model = form.defaultModel.trim();
  return {
    provider: form.provider, default_model: model,
    planning_model: oneModel ? model : form.planningModel.trim() || model,
    subplan_model: oneModel ? model : form.subplanModel.trim() || model,
    implementation_model: oneModel ? model : form.implementationModel.trim() || model,
    base_url: form.baseUrl.trim(), api_key: form.apiKey.trim(),
  };
}
function origin(provider: string, address: string) {
  try { return new URL(address || DEFAULT_URLS[provider]).origin; } catch { return ''; }
}
function displayProvider(provider: string, address: string) {
  const services: Record<string, string> = {
    'api.deepseek.com': 'DeepSeek', 'api.openai.com': 'OpenAI',
    'api.anthropic.com': 'Anthropic', 'generativelanguage.googleapis.com': 'Google Gemini',
  };
  try {
    const host = new URL(address || DEFAULT_URLS[provider]).hostname;
    return services[host] || host;
  } catch { return 'Custom API'; }
}

export default function SettingsPage() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState(EMPTY_FORM);
  const [oneModel, setOneModel] = useState(true);
  const [saveError, setSaveError] = useState('');
  const hasEdits = useRef(false);
  const { data: settings, isLoading, isError, refetch } = useQuery({
    queryKey: ['settings'], queryFn: configApi.getSettings,
    refetchOnWindowFocus: false, refetchInterval: 10000,
  });
  useEffect(() => {
    if (!settings || hasEdits.current) return;
    const saved = savedForm(settings);
    setForm(saved); setOneModel(usesOneModel(saved));
  }, [settings]);
  const updateMutation = useMutation({
    mutationFn: configApi.updateLLMConfig,
    onSuccess: () => {
      hasEdits.current = false;
      setForm(current => ({ ...current, apiKey: '' }));
      setSaveError('');
      void queryClient.invalidateQueries({ queryKey: ['settings'] });
      void queryClient.invalidateQueries({ queryKey: ['llm-providers'] });
      toast.success('Settings saved', 'This configuration will be used when you start or resume a run.');
    },
    onError: (error) => {
      const message = isAxiosError(error) && typeof error.response?.data?.detail === 'string'
        ? error.response.data.detail : 'Could not save settings. Check the backend connection and try again.';
      setSaveError(message);
    },
  });
  const edit = (changes: Partial<SettingsForm>) => {
    hasEdits.current = true; setSaveError('');
    setForm(current => ({ ...current, ...changes }));
  };
  const reset = () => {
    if (!settings) return;
    const saved = savedForm(settings);
    hasEdits.current = false; setForm(saved); setOneModel(usesOneModel(saved)); setSaveError('');
  };
  const selectProvider = (provider: string) => {
    if (!settings) return;
    const saved = savedForm(settings, provider);
    edit(saved); setOneModel(usesOneModel(saved));
  };

  if (isLoading) return <div className="flex min-h-screen items-center justify-center bg-[#f7efe0] text-stone-600" role="status">Loading saved settings…</div>;
  if (!settings || isError) return <div className="min-h-screen bg-[#f7efe0] p-8"><div className="mx-auto max-w-3xl space-y-4">
    <h1 className="text-2xl font-semibold">Settings unavailable</h1>
    <p>Check that the DeepRepro backend is running. Your saved configuration has not changed.</p>
    <Button onClick={() => void refetch()}>Try again</Button>
  </div></div>;

  const baseline = savedForm(settings);
  const dirty = JSON.stringify(payload(form, oneModel)) !== JSON.stringify(payload(baseline, usesOneModel(baseline)));
  const keySaved = Boolean(settings.api_key_configured?.[form.provider]);
  const savedKey = Boolean(settings.api_key_configured?.[settings.llm_provider]);
  const addressChanged = keySaved && origin(form.provider, form.baseUrl.trim()) !== origin(form.provider, settings.api_base_urls?.[form.provider] || '');
  const locked = Boolean(settings.configuration_locked);
  const readOnly = locked || updateMutation.isPending;
  let validation = '';
  if (!form.defaultModel.trim()) validation = 'Enter a model ID before saving.';
  else if (!form.apiKey.trim() && (!keySaved || addressChanged)) validation = addressChanged
    ? 'Enter an API key for the new API host. The saved key will not be reused automatically.'
    : 'Enter an API key for this connection.';
  if (form.baseUrl.trim()) {
    try {
      const url = new URL(form.baseUrl.trim());
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new Error();
    } catch { validation = 'Enter a valid HTTP(S) base URL without credentials or query parameters.'; }
  }
  const roleFields = [
    ['planningModel', 'Paper planning', 'Builds the overall reproduction plan and file structure.'],
    ['subplanModel', 'Round planning', 'Chooses the next files and repairs in DeepPlan mode.'],
    ['implementationModel', 'Code implementation', 'Writes files and applies repairs.'],
  ] as const;

  return <div className="min-h-screen bg-[#f7efe0]"><div className="mx-auto max-w-4xl space-y-5 px-5 py-7 sm:px-8">
    <Link to="/" className="inline-flex items-center gap-2 text-sm text-stone-600 hover:text-stone-950"><ArrowLeft className="h-4 w-4" />Back to workspace</Link>
    <header>
      <h1 className="font-serif text-3xl font-semibold text-stone-950 sm:text-4xl">Model & API settings</h1>
      <p className="mt-2 text-sm leading-6 text-stone-600">Connect your API and choose a model. Saved changes apply when you start or resume a run.</p>
    </header>

    <section aria-label="Saved configuration" className="rounded-3xl bg-stone-950 p-5 text-white">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-xs font-medium uppercase tracking-widest text-stone-400">Currently saved</p>
          <p className="mt-2 text-xl font-semibold">{displayProvider(settings.llm_provider, settings.api_base_urls?.[settings.llm_provider] || '')}<span className="mx-2 text-stone-500">/</span><span className="font-mono text-base text-emerald-200">{settings.models.default || 'No model set'}</span></p>
        </div>
        <span className={`inline-flex items-center gap-2 rounded-full px-3 py-1.5 text-xs font-medium ${savedKey ? 'bg-emerald-400/15 text-emerald-200' : 'bg-amber-400/15 text-amber-200'}`}><KeyRound className="h-3.5 w-3.5" />{savedKey ? 'API key saved' : 'API key missing'}</span>
      </div>
      <p className="mt-3 break-all text-xs text-stone-400">{settings.api_base_urls?.[settings.llm_provider] || 'Standard API address'}</p>
    </section>

    {locked && <p role="status" className="rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">A task is running. Pause it before changing model settings.</p>}
    <form onSubmit={event => { event.preventDefault(); if (!validation && dirty && !readOnly) updateMutation.mutate(payload(form, oneModel)); }} className="space-y-5">
      <fieldset disabled={readOnly} className="rounded-3xl border border-stone-200 bg-white/85 p-5 sm:p-6 disabled:opacity-70">
        <legend className="sr-only">API connection</legend>
        <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
          <div><h2 className="text-lg font-semibold text-stone-950">1. API connection</h2><p className="mt-1 text-sm text-stone-500">Choose the API format. DeepSeek uses OpenAI-compatible; the base URL selects the service.</p></div>
          <button type="button" onClick={() => { edit({provider: 'openai', baseUrl: 'https://api.deepseek.com', defaultModel: 'deepseek-flash', planningModel: 'deepseek-flash', subplanModel: 'deepseek-flash', implementationModel: 'deepseek-flash', apiKey: ''}); setOneModel(true); }} className="rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs font-semibold text-emerald-800 hover:bg-emerald-100">Use DeepSeek Flash</button>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="space-y-2 text-sm font-medium text-stone-800"><span>API format</span><select aria-label="API format" value={form.provider} onChange={e => selectProvider(e.target.value)} className="input h-12 rounded-xl">{Object.entries(API_FORMATS).map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label>
          <label className="space-y-2 text-sm font-medium text-stone-800"><span>API base URL</span><input aria-label="API base URL" type="text" value={form.baseUrl} onChange={e => edit({baseUrl:e.target.value})} placeholder={DEFAULT_URLS[form.provider]} className="input h-12 rounded-xl" /><span className="block text-xs font-normal text-stone-500">Leave blank for the standard API address. DeepSeek requires its own base URL.</span></label>
          <label className="space-y-2 text-sm font-medium text-stone-800 sm:col-span-2"><span>API key</span><input aria-label="API key" type="password" autoComplete="off" spellCheck={false} value={form.apiKey} onChange={e => edit({apiKey:e.target.value})} placeholder={keySaved ? 'Leave blank to keep the saved key' : 'Enter your API key'} className="input h-12 rounded-xl" /><span className="block text-xs font-normal text-stone-500">{keySaved ? 'A key is saved for this API configuration. Leave blank to keep it, or enter a replacement.' : 'No key is saved for this API format.'} Stored on this computer; never displayed here.</span></label>
        </div>
      </fieldset>

      <fieldset disabled={readOnly} className="rounded-3xl border border-stone-200 bg-white/85 p-5 sm:p-6 disabled:opacity-70">
        <legend className="sr-only">Model selection</legend>
        <h2 className="text-lg font-semibold text-stone-950">2. Model selection</h2>
        <label className="mt-4 block space-y-2 text-sm font-medium text-stone-800"><span>Default model</span><input aria-label="Default model" value={form.defaultModel} onChange={e => edit({defaultModel:e.target.value})} placeholder={form.provider === 'openai' ? 'e.g. deepseek-flash' : 'Model ID from your API provider'} className="input h-12 rounded-xl" /><span className="block text-xs font-normal text-stone-500">Used for paper analysis and as the default for all agents. Enter the exact model ID from your API provider.</span></label>
        <label className="mt-5 flex cursor-pointer items-start gap-3 rounded-2xl border border-stone-200 bg-stone-50 p-4"><input aria-label="Use the same model for all stages" type="checkbox" checked={oneModel} onChange={e => { hasEdits.current = true; setOneModel(e.target.checked); }} className="mt-1 h-4 w-4 accent-emerald-700" /><span><span className="text-sm font-semibold text-stone-900">Use the same model for all stages</span><span className="mt-1 block text-xs leading-5 text-stone-500">A simple setup for the demo. Uncheck to choose separate models for planning and code generation.</span></span></label>
        {!oneModel && <div className="mt-5 space-y-4 border-t border-stone-200 pt-4"><p className="flex items-center gap-2 text-sm font-medium text-stone-700"><Settings2 className="h-4 w-4" />Per-stage models <span className="text-xs font-normal text-stone-500">Optional overrides</span></p>{roleFields.map(([field, label, hint]) => <label key={field} className="block space-y-2 text-sm font-medium text-stone-800"><span>{label}</span><input aria-label={label} value={form[field]} onChange={e => edit({[field]:e.target.value})} placeholder={form.defaultModel || 'Use the default model'} className="input h-11 rounded-xl" /><span className="block text-xs font-normal text-stone-500">{hint} Leave blank to use the default model.</span></label>)}</div>}
      </fieldset>

      <div className="rounded-3xl border border-stone-200 bg-white/90 p-5">
        {validation && <p role="alert" className="mb-3 text-sm text-amber-800">{validation}</p>}
        {saveError && <p role="alert" className="mb-3 text-sm text-red-700">{saveError}</p>}
        <div className="flex flex-wrap items-center justify-between gap-4">
          <p aria-live="polite" className={`inline-flex items-center gap-2 text-sm ${dirty ? 'text-amber-800' : 'text-stone-600'}`}>{!dirty && <CheckCircle2 className="h-4 w-4 text-emerald-700" />}{dirty ? 'Unsaved changes' : 'Saved configuration loaded'}</p>
          <div className="flex items-center gap-3">{dirty && <Button type="button" variant="ghost" onClick={reset} disabled={readOnly}>Discard changes</Button>}<Button type="submit" disabled={!dirty || Boolean(validation) || locked} isLoading={updateMutation.isPending} className="h-11 rounded-xl bg-stone-950 px-5 hover:bg-stone-800"><Save className="mr-2 h-4 w-4" />Save settings</Button></div>
        </div>
        <p className="mt-3 text-xs text-stone-500">Saving stores your configuration; it does not test the API connection.</p>
      </div>
    </form>
  </div></div>;
}
