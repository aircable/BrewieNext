export type Edge = {
  id: string;
  source: string;
  target: string;
  label?: string;
  condition?: string;
  choice_key?: string;
  choice_value?: string;
};

export type GraphNode = {
  id: string;
  label: string;
  procedure: string;
  description: string;
  parameters: Record<string, unknown>;
  position: { x: number; y: number };
  parallel_group?: string;
  parallel_role?: 'primary' | 'concurrent';
};

export type BrewGraph = {
  name: string;
  description: string;
  default_recipe?: string;
  entry_point: string;
  source_format?: 'sequence' | 'graph';
  nodes: GraphNode[];
  edges: Edge[];
};

export type ProgramSummary = {
  id: string;
  label: string;
  category: 'brewing' | 'cleaning' | 'maintenance' | 'diagnostics';
  description: string;
  status: 'design' | 'available';
  workflow?: string;
};

export type ProcedureState = {
  id: string;
  description: string;
  actions: Record<string, unknown>[];
  conditions?: string[];
  on_exit?: Record<string, unknown>[];
  timeout_s?: string;
  transitions: { condition?: string; default?: string; then: string }[];
};

export type ProcedureDocument = {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
  start_state: string;
  error_handler?: string;
  states: ProcedureState[];
  raw?: Record<string, unknown>;
};

export type BrewieScreen = {
  title: string;
  message: string;
  footer_message: string;
  status: 'idle' | 'running' | 'waiting_for_input' | 'paused' | 'error' | 'complete';
  readouts: { label: string; value: string; unit?: string }[];
  choices: { value: string; label: string }[];
  progress: number | null;
  allowed_controls: string[];
};

export type BrewSession = {
  graph_id: string;
  active_node: string;
  active_procedure: string;
  active_state: string;
  status: BrewieScreen['status'];
  screen: BrewieScreen;
};

export type RecipeSummary = {
  id: string;
  name: string;
  style: string;
  batch_volume_L: number;
  source: 'bundled' | 'user';
};

export type Fermentable = {
  name: string;
  amount_kg: number;
  bag?: string | number;
};

export type MashStep = {
  name: string;
  target_temperature_C: number;
  duration_min: number;
};

export type HopAddition = {
  cage: number;
  hop?: string;
  amount_g?: number;
  minutes_remaining: number;
};

export type Recipe = {
  schema_version: 1;
  id: string;
  name: string;
  description?: string;
  style?: string;
  batch_volume_L: number;
  water: { mash_volume_L: number; sparge_volume_L: number };
  fermentables: Fermentable[];
  mash: { mash_in_temperature_C: number; steps: MashStep[] };
  sparge?: { target_temperature_C: number; cycle_count: number };
  boil: {
    duration_min: number;
    target_temperature_C: number;
    hop_additions: HopAddition[];
  };
  cooling: { target_temperature_C: number };
  sedimentation: { duration_min: number };
  yeasts?: { name: string; amount_g?: number }[];
  notes?: string;
};

export const demoGraph: BrewGraph = {
  name: 'beer_brewing',
  description: 'Main BrewieNext ordered brewing workflow',
  default_recipe: 'development_test',
  entry_point: 'prepare_brew',
  source_format: 'sequence',
  nodes: [
    ['prepare_brew', 'Prepare brew', 'prepare_brew', 360, 70],
    ['fill_mash_water', 'Fill mash water', 'fill_mash_water', 360, 210],
    ['heat_mash_water', 'Heat mash water', 'heat_mash_water', 360, 350],
    ['transfer_to_mash', 'Transfer to mash', 'transfer_water_to_mash', 360, 490],
    ['fill_sparge_water', 'Fill sparge water', 'fill_sparge_water', 360, 630],
    ['mashing', 'Two-rest mashing', 'two_rest_mashing', 220, 770],
    ['heat_sparge_water', 'Heat sparge water', 'heat_sparge_water', 500, 770],
    ['sparging', 'Sparging', 'sparging', 360, 940],
    ['boiling', 'Boiling', 'boiling', 360, 1080],
    ['hopping', 'Hopping', 'hopping', 360, 1220],
    ['cooling', 'Cooling', 'cooling', 360, 1360],
    ['sedimentation', 'Sedimentation', 'sedimentation', 360, 1500],
    ['transfer_to_fermenter', 'Transfer to fermenter', 'transfer_to_fermenter', 360, 1640]
  ].map(([id, label, procedure, x, y]) => ({
    id: id as string,
    label: label as string,
    procedure: procedure as string,
    description: `${label} procedure`,
    parameters: {},
    position: { x: x as number, y: y as number },
    ...((id === 'mashing' || id === 'heat_sparge_water') ? {
      parallel_group: 'mash_and_sparge_heat',
      parallel_role: id === 'mashing' ? 'primary' as const : 'concurrent' as const
    } : {})
  })),
  edges: [
    ['prepare_brew', 'fill_mash_water'],
    ['fill_mash_water', 'heat_mash_water'],
    ['heat_mash_water', 'transfer_to_mash'],
    ['transfer_to_mash', 'fill_sparge_water'],
    ['fill_sparge_water', 'mashing'],
    ['fill_sparge_water', 'heat_sparge_water'],
    ['mashing', 'sparging'],
    ['heat_sparge_water', 'sparging'],
    ['sparging', 'boiling'],
    ['boiling', 'hopping'],
    ['hopping', 'cooling'],
    ['cooling', 'sedimentation'],
    ['sedimentation', 'transfer_to_fermenter']
  ].map(([source, target], index) => ({ id: `edge-${index}`, source, target }))
};

export const demoSession: BrewSession = {
  graph_id: 'beer_brewing',
  active_node: 'heat_mash_water',
  active_procedure: 'heat_mash_water',
  active_state: 'heat_to_target',
  status: 'running',
  screen: {
    title: 'Heating mash water',
    message: 'Heating to the mash target temperature.',
    footer_message: '',
    status: 'running',
    readouts: [],
    choices: [],
    progress: 42,
    allowed_controls: ['pause', 'abort']
  }
};

export const demoProcedure: ProcedureDocument = {
  name: 'heat_mash_water',
  description: 'Heat mash water to the configured target.',
  parameters: { target_temp_C: 72, temp_tolerance_C: 1 },
  start_state: 'check_water_level',
  states: [
    { id: 'check_water_level', description: 'Verify sufficient water is available.', actions: [{ read_sensor: 'weight_boil_tank' }], transitions: [{ condition: 'weight_boil_tank >= minimum', then: 'heat_to_target' }] },
    { id: 'heat_to_target', description: 'Enable the heater and wait for the target temperature.', actions: [{ set_heater: { device: 'mash_heater', state: 'on' } }], timeout_s: '3900s', transitions: [{ condition: 'temperature_reached', then: 'complete' }] },
    { id: 'complete', description: 'Heating completed safely.', actions: [], transitions: [{ default: 'next_phase', then: 'next_phase' }] }
  ]
};
