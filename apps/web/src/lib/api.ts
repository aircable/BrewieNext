import { demoGraph, demoProcedure, type BrewGraph, type BrewieScreen, type ProcedureDocument, type ProgramSummary, type Recipe, type RecipeSummary } from './model';
import type { MachineStatus } from './simulation';

export type RuntimeProcedure = {
  node_id: string;
  procedure: string;
  state: string;
  status: string;
  elapsed_s: number;
  state_elapsed_s: number;
  input: { key: string; options: string[] } | null;
  error: string | null;
};

export type RuntimeStatus = {
  id: string;
  mode: 'simulation' | 'hardware';
  status: 'running' | 'waiting_for_input' | 'paused' | 'complete' | 'error' | 'aborted';
  speed: number;
  elapsed_s: number;
  workflow: string;
  step_index: number;
  step_count: number;
  active_nodes: string[];
  completed_nodes: string[];
  active_procedure: string | null;
  active_state: string | null;
  procedures: RuntimeProcedure[];
  screen: BrewieScreen;
  machine: MachineStatus;
  error: string | null;
};

// Static assets are served on 8080. The optional Flask editor API uses 8081
// so the two services can run independently on the embedded image.
const API_BASE = import.meta.env.VITE_API_BASE_URL ||
  (import.meta.env.DEV ? '' : `http://${window.location.hostname}:8081`);

function request<T>(path: string, options?: RequestInit): Promise<T> {
  // The target's Qt WebKit predates fetch(). XMLHttpRequest keeps the real
  // touchscreen and the desktop preview on the same API transport.
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open(options?.method || 'GET', `${API_BASE}${path}`, true);
    xhr.setRequestHeader('Content-Type', 'application/json');
    xhr.onreadystatechange = () => {
      if (xhr.readyState !== 4) return;
      let payload: any = null;
      try {
        payload = xhr.responseText ? JSON.parse(xhr.responseText) : null;
      } catch {
        // Preserve the HTTP status when an older backend returns no JSON body.
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(payload as T);
        return;
      }
      reject(new Error(payload?.error || `${xhr.status || 0} ${xhr.statusText || 'API request failed'}`));
    };
    xhr.onerror = () => reject(new Error('API connection failed'));
    xhr.send(typeof options?.body === 'string' ? options.body : null);
  });
}

export async function loadPrograms(useFallback = true): Promise<ProgramSummary[]> {
  try {
    const result = await request<{ programs: ProgramSummary[] }>('/api/programs');
    return result.programs;
  } catch (error) {
    if (!useFallback) throw error;
    return [{
      id: 'beer_brewing',
      label: 'All-grain brewing',
      category: 'brewing',
      description: demoGraph.description,
      status: 'available',
      workflow: 'beer_brewing'
    }];
  }
}

export async function loadGraph(name = 'beer_brewing'): Promise<BrewGraph> {
  try {
    const result = await request<{ data: BrewGraph }>(`/api/graphs/${encodeURIComponent(name)}`);
    return result.data;
  } catch {
    if (name === demoGraph.name) return demoGraph;
    throw new Error(`Workflow '${name}' is not available`);
  }
}

export async function addWorkflowStep(graphName: string, step: { id: string; label: string; procedure: string; after?: string }): Promise<BrewGraph> {
  const result = await request<{ data: BrewGraph }>(`/api/graphs/${encodeURIComponent(graphName)}/steps`, {
    method: 'POST',
    body: JSON.stringify(step)
  });
  return result.data;
}

export async function removeWorkflowStep(graphName: string, stepId: string): Promise<BrewGraph> {
  const result = await request<{ data: BrewGraph }>(`/api/graphs/${encodeURIComponent(graphName)}/steps/${encodeURIComponent(stepId)}`, {
    method: 'DELETE'
  });
  return result.data;
}

export async function updateWorkflowStep(graphName: string, stepId: string, changes: { label: string; description: string }): Promise<BrewGraph> {
  const result = await request<{ data: BrewGraph }>(`/api/graphs/${encodeURIComponent(graphName)}/steps/${encodeURIComponent(stepId)}`, {
    method: 'PATCH',
    body: JSON.stringify(changes)
  });
  return result.data;
}

export async function resolveGraph(recipeId: string, graphName = 'beer_brewing'): Promise<BrewGraph> {
  const result = await request<{ data: BrewGraph }>(`/api/graphs/${encodeURIComponent(graphName)}/resolve?recipe=${encodeURIComponent(recipeId)}`);
  return result.data;
}

export async function loadRecipes(): Promise<RecipeSummary[]> {
  const result = await request<{ recipes: RecipeSummary[] }>('/api/recipes');
  return result.recipes;
}

export async function loadRecipe(recipeId: string): Promise<Recipe> {
  const result = await request<{ data: Recipe }>(`/api/recipes/${encodeURIComponent(recipeId)}`);
  return result.data;
}

export async function validateRecipe(recipe: Recipe): Promise<{ valid: boolean; errors: string[]; warnings: string[] }> {
  return request(`/api/recipes/${encodeURIComponent(recipe.id)}/validate`, {
    method: 'POST',
    body: JSON.stringify({ data: recipe })
  });
}

export async function saveRecipe(recipe: Recipe): Promise<void> {
  await request(`/api/recipes/${encodeURIComponent(recipe.id)}`, {
    method: 'PUT',
    body: JSON.stringify(recipe)
  });
}

export async function loadProcedure(name: string): Promise<ProcedureDocument | null> {
  try {
    const result = await request<{ data: ProcedureDocument }>(`/api/procedures/${name}`);
    return normalizeProcedure(result.data, name);
  } catch {
    return name === demoProcedure.name ? demoProcedure : null;
  }
}

export async function createProcedure(name: string, label: string): Promise<void> {
  await request('/api/procedures', {
    method: 'POST',
    body: JSON.stringify({
      name,
      description: label,
      start_state: 'start',
      states: {
        start: {
          description: 'Describe the first state.',
          action: [],
          transition: [{ default: 'next_phase' }]
        }
      },
      error_handler: `${name}-error`
    })
  });
}

function normalizeProcedure(data: ProcedureDocument, resourceName?: string): ProcedureDocument {
  const rawStates = (data as unknown as { states: Record<string, any> }).states;
  if (Array.isArray(rawStates)) return data;
  return {
    name: resourceName || (data as any).name || (data as any).phase,
    description: (data as any).description || '',
    parameters: (data as any).parameters || {},
    start_state: (data as any).start_state || Object.keys(rawStates || {})[0] || '',
    error_handler: (data as any).error_handler || (data as any).error,
    raw: data as unknown as Record<string, unknown>,
    states: Object.entries(rawStates || {}).map(([id, value]) => ({
      id,
      description: value.description || '',
      actions: value.action || value.actions || [],
      conditions: value.conditions || [],
      on_exit: value.on_exit || [],
      timeout_s: value.timeout_s,
      transitions: (value.transitions || value.transition || []).map((transition: any) => {
        if (typeof transition === 'string') return { then: transition };
        if (transition.default !== undefined) return { default: transition.default, then: transition.default };
        if (transition.condition !== undefined || transition.if !== undefined) {
          return {
            condition: transition.condition ?? transition.if,
            then: transition.then ?? transition.target
          };
        }
        const entry = Object.entries(transition)[0];
        return entry ? { condition: entry[0], then: entry[1] as string } : { condition: '', then: '' };
      })
    }))
  };
}

export async function validateDraft(name: string, data: unknown): Promise<{ valid: boolean; issues: { severity: string; message: string }[] }> {
  return request(`/api/procedures/${name}/validate`, {
    method: 'POST',
    body: JSON.stringify({ data: toWireProcedure(data as ProcedureDocument) })
  });
}

export async function saveProcedure(name: string, data: unknown): Promise<void> {
  await request(`/api/procedures/${name}`, {
    method: 'PUT',
    body: JSON.stringify(toWireProcedure(data as ProcedureDocument))
  });
}

export async function loadMachineStatus(): Promise<MachineStatus | null> {
  try {
    const result = await request<{ data: MachineStatus }>('/api/machine/status');
    return result.data;
  } catch {
    return null;
  }
}

export async function sendMachineCommand(command: Record<string, unknown>): Promise<MachineStatus> {
  const result = await request<{ data: MachineStatus }>('/api/machine/command', {
    method: 'POST',
    body: JSON.stringify(command)
  });
  return result.data;
}

export async function startRuntime(options: {
  mode: 'simulation' | 'hardware';
  recipe_id: string;
  workflow?: string;
  speed?: number;
  confirm_hardware?: boolean;
}): Promise<RuntimeStatus> {
  const result = await request<{ data: RuntimeStatus }>('/api/runtime/sessions', {
    method: 'POST',
    body: JSON.stringify({ workflow: options.workflow || 'beer_brewing', ...options })
  });
  return result.data;
}

export async function loadRuntimeStatus(): Promise<RuntimeStatus | null> {
  try {
    const result = await request<{ data: RuntimeStatus }>('/api/runtime/session');
    return result.data;
  } catch {
    return null;
  }
}

export async function controlRuntime(action: 'pause' | 'resume' | 'abort' | 'set_speed', speed?: number): Promise<RuntimeStatus> {
  const result = await request<{ data: RuntimeStatus }>('/api/runtime/session/control', {
    method: 'POST',
    body: JSON.stringify({ action, ...(speed === undefined ? {} : { speed }) })
  });
  return result.data;
}

export async function provideRuntimeInput(key: string, value: string, procedure?: string): Promise<RuntimeStatus> {
  const result = await request<{ data: RuntimeStatus }>('/api/runtime/session/input', {
    method: 'POST',
    body: JSON.stringify({ key, value, ...(procedure ? { procedure } : {}) })
  });
  return result.data;
}

export async function navigateRuntime(direction: 'previous' | 'next'): Promise<RuntimeStatus> {
  const result = await request<{ data: RuntimeStatus }>('/api/runtime/session/navigate', {
    method: 'POST',
    body: JSON.stringify({ direction })
  });
  return result.data;
}

function toWireProcedure(data: ProcedureDocument): Record<string, unknown> {
  const raw = { ...(data.raw || {}) };
  // Procedure cleanup is state-scoped. Never preserve obsolete hidden
  // procedure-level finalize blocks from documents loaded before migration.
  delete raw.finalize;
  const recipeGlobalProcedure = ['fill_mash_water', 'fill_sparge_water', 'heat_mash_water'].includes(data.name);
  if (recipeGlobalProcedure) {
    delete raw.parameters;
    delete raw.phase;
    delete raw.error;
  }
  const states: Record<string, unknown> = {};
  for (const state of data.states) {
    states[state.id] = {
      description: state.description,
      action: state.actions,
      ...(state.conditions?.length ? { conditions: state.conditions } : {}),
      ...(state.on_exit?.length ? { on_exit: state.on_exit } : {}),
      ...(state.timeout_s ? { timeout_s: state.timeout_s } : {}),
      transition: state.transitions.map((transition) => transition.default !== undefined
        ? { default: transition.then }
        : transition.condition
          ? { [transition.condition]: transition.then }
          : { default: transition.then })
    };
  }
  return {
    ...raw,
    name: data.name,
    description: data.description,
    ...(!recipeGlobalProcedure && Object.keys(data.parameters || {}).length ? { parameters: data.parameters } : {}),
    start_state: data.start_state,
    states,
    error_handler: data.error_handler || `${data.name}-error`
  };
}
