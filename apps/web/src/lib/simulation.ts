import type { BrewGraph, BrewSession, ProcedureDocument, Recipe } from './model';

export type HeaterStatus = { active: boolean; targetC: number | null; powerPercent: number; state?: 'off' | 'on' | 'deferred' };
export type MachineStatus = {
  source: 'simulation' | 'hardware';
  connected: boolean;
  serialDevice?: string;
  lastError?: string | null;
  valves: Record<string, boolean>;
  pumps: Record<string, boolean>;
  heaters: Record<string, HeaterStatus>;
  sensors: {
    tempMashC: number;
    tempBoilC: number;
    boilVolumeL: number;
    mashVolumeL: number;
    systemWeightKg: number;
    mashPumpTacho: number;
    boilPumpTacho: number;
  };
};

type SensorTargets = Partial<Pick<MachineStatus['sensors'], 'tempMashC' | 'tempBoilC' | 'boilVolumeL' | 'mashVolumeL'>>;
type SimulationStep = {
  id: string;
  nodeIds: string[];
  procedure: string;
  state: string;
  title: string;
  message: string;
  footerMessage: string;
  readouts: ReadoutDefinition[];
  waitForInput?: UserInputDefinition;
  durationS: number;
  actions: Record<string, unknown>[];
  onExit: Record<string, unknown>[];
  targets: SensorTargets;
};

type ReadoutDefinition = { label: string; sensor?: string; global?: string; unit?: string };
type UserInputDefinition = { id: string; key: string; options: string[] };

export type SimulationPlan = { steps: SimulationStep[]; totalSeconds: number };
export type SimulationSnapshot = {
  elapsedSeconds: number;
  totalSeconds: number;
  progress: number;
  activeNodeIds: string[];
  completedNodeIds: string[];
  activeStep: SimulationStep;
  session: BrewSession;
  machine: MachineStatus;
};

const VALVES = [
  'water_inlet_valve', 'mash_inlet_valve', 'boil_inlet_valve',
  'hop_cage_1_valve', 'hop_cage_2_valve', 'hop_cage_3_valve', 'hop_cage_4_valve',
  'cooling_water_inlet_valve', 'wort_cooling_valve', 'outlet_valve',
  'mash_return_valve', 'boil_return_valve'
];

const ALIASES: Record<string, string> = {
  water_inlet: 'water_inlet_valve', cooling_valve: 'wort_cooling_valve',
  cool_out_valve: 'cooling_water_inlet_valve', 'mash-pump': 'mash_pump',
  'boil-pump': 'boil_pump', 'mash-heater': 'mash_heater', 'boil-heater': 'boil_heater'
};

export function createIdleMachineStatus(): MachineStatus {
  return {
    source: 'simulation', connected: true,
    valves: Object.fromEntries(VALVES.map((id) => [id, false])),
    pumps: { mash_pump: false, boil_pump: false },
    heaters: {
      mash_heater: { active: false, targetC: null, powerPercent: 0 },
      boil_heater: { active: false, targetC: null, powerPercent: 0 }
    },
    sensors: {
      tempMashC: 20, tempBoilC: 20, boilVolumeL: 0, mashVolumeL: 0,
      systemWeightKg: 0, mashPumpTacho: 0, boilPumpTacho: 0
    }
  };
}

function recipeGlobals(recipe: Recipe) {
  const hops = [...recipe.boil.hop_additions].sort((a, b) => a.cage - b.cage);
  const hoppingDuration = Math.max(0, ...hops.map((hop) => hop.minutes_remaining));
  const totalWaterVolume = recipe.water.mash_volume_L + recipe.water.sparge_volume_L;
  return {
    recipe_id: recipe.id,
    recipe_name: recipe.name,
    batch_volume_L: recipe.batch_volume_L,
    mash_water_volume_L: recipe.water.mash_volume_L,
    sparge_water_volume_L: recipe.water.sparge_volume_L,
    mash_in_temperature_C: recipe.mash.mash_in_temperature_C,
    mash_rest_1_temperature_C: recipe.mash.steps[0]?.target_temperature_C ?? 67,
    mash_rest_1_duration_min: recipe.mash.steps[0]?.duration_min ?? 60,
    mash_rest_2_temperature_C: recipe.mash.steps[1]?.target_temperature_C ?? 76,
    mash_rest_2_duration_min: recipe.mash.steps[1]?.duration_min ?? 10,
    mash_total_duration_min: recipe.mash.steps.reduce((sum, step) => sum + step.duration_min, 0),
    sparge_target_temperature_C: recipe.sparge?.target_temperature_C ?? 78,
    sparge_cycle_count: recipe.sparge?.cycle_count ?? 5,
    sparge_boil_reserve_volume_L: Math.max(0, totalWaterVolume - 20),
    boil_duration_min: recipe.boil.duration_min,
    boil_target_temperature_C: recipe.boil.target_temperature_C,
    hopping_duration_min: hoppingDuration,
    initial_unhopped_boil_duration_min: Math.max(0, recipe.boil.duration_min - hoppingDuration),
    cooling_target_temperature_C: recipe.cooling.target_temperature_C,
    sedimentation_duration_min: recipe.sedimentation.duration_min,
    hop_cage_1_minutes_remaining: hops[0]?.minutes_remaining ?? 60,
    hop_cage_2_minutes_remaining: hops[1]?.minutes_remaining ?? 30,
    hop_cage_3_minutes_remaining: hops[2]?.minutes_remaining ?? 10,
    hop_cage_4_minutes_remaining: hops[3]?.minutes_remaining ?? 5
  };
}

function normalizeId(id: unknown) { return ALIASES[String(id)] || String(id); }
function resolveNumber(value: unknown, globals: Record<string, unknown>): number | null {
  if (typeof value === 'number') return value;
  if (typeof value === 'string' && typeof globals[value] === 'number') return globals[value];
  return null;
}

function applyActions(machine: MachineStatus, actions: Record<string, unknown>[], globals: Record<string, unknown>) {
  for (const action of actions) {
    const [kind, raw] = Object.entries(action)[0] || [];
    const value = raw as Record<string, unknown> | undefined;
    if (!kind || !value || typeof value !== 'object') continue;
    if (kind === 'set_valve') {
      const id = normalizeId(value.valve);
      machine.valves[id] = ['open', 'on', true].includes(value.state as never);
    } else if (kind === 'set_pump') {
      const id = normalizeId(value.device);
      machine.pumps[id] = ['on', true].includes(value.state as never);
    } else if (kind === 'set_heater' || kind === 'enable_pid') {
      const id = normalizeId(value.device);
      const heater = machine.heaters[id];
      if (!heater) continue;
      const target = resolveNumber(value.target_temp, globals);
      const off = ['off', false].includes(value.state as never);
      heater.active = !off;
      heater.targetC = off ? null : target ?? heater.targetC;
      heater.powerPercent = off ? 0 : 100;
    } else if (kind === 'disable_pid') {
      const heater = machine.heaters[normalizeId(value.device)];
      if (heater) heater.powerPercent = heater.active ? 35 : 0;
    } else if (kind === 'run_hop_stage') {
      const cages = Array.isArray(value.open_cages) ? value.open_cages : [];
      for (let cage = 1; cage <= 4; cage += 1) machine.valves[`hop_cage_${cage}_valve`] = cages.includes(cage);
      machine.valves.boil_return_valve = true;
      machine.pumps.boil_pump = true;
      machine.heaters.boil_heater = { active: true, targetC: resolveNumber('boil_target_temperature_C', globals), powerPercent: 70 };
    } else if (kind === 'finish_avr_step_session') {
      for (let cage = 1; cage <= 4; cage += 1) machine.valves[`hop_cage_${cage}_valve`] = false;
      machine.valves.boil_return_valve = false;
      machine.pumps.boil_pump = false;
      machine.heaters.boil_heater = { active: false, targetC: null, powerPercent: 0 };
    }
  }
}

function notification(state: ProcedureDocument['states'][number] | undefined, fallback: string) {
  const notify = state?.actions.find((action) => 'notify_user' in action)?.notify_user;
  if (!notify || typeof notify !== 'object') return { message: fallback, footerMessage: '', readouts: [] as ReadoutDefinition[] };
  const value = notify as { text?: unknown; footer_text?: unknown; readouts?: unknown };
  const readouts = Array.isArray(value.readouts)
    ? value.readouts.flatMap((candidate): ReadoutDefinition[] => {
        if (!candidate || typeof candidate !== 'object') return [];
        const item = candidate as Record<string, unknown>;
        if (typeof item.label !== 'string') return [];
        return [{
          label: item.label,
          ...(typeof item.sensor === 'string' ? { sensor: item.sensor } : {}),
          ...(typeof item.global === 'string' ? { global: item.global } : {}),
          ...(typeof item.unit === 'string' ? { unit: item.unit } : {})
        }];
      })
    : [];
  return {
    message: value.text === undefined ? fallback : String(value.text),
    footerMessage: value.footer_text === undefined ? '' : String(value.footer_text),
    readouts
  };
}

function userInput(procedure: string, stateId: string, state: ProcedureDocument['states'][number] | undefined): UserInputDefinition | undefined {
  const action = state?.actions.find((candidate) => 'wait_for_user_input' in candidate)?.wait_for_user_input;
  if (!action) return undefined;
  const params = typeof action === 'object' && action ? action as Record<string, unknown> : {};
  const notify = state?.actions.find((candidate) => 'notify_user' in candidate)?.notify_user;
  const notifyOptions = typeof notify === 'object' && notify && Array.isArray((notify as Record<string, unknown>).options)
    ? (notify as { options: unknown[] }).options
    : [];
  const expected = Array.isArray(params.expected_values) ? params.expected_values : notifyOptions;
  const options = expected.filter((value): value is string => typeof value === 'string');
  if (!options.length) return undefined;
  return {
    id: `${procedure}:${stateId}`,
    key: typeof params.key === 'string' ? params.key : `${stateId}_response`,
    options
  };
}

function resolvedReadouts(definitions: ReadoutDefinition[], machine: MachineStatus, globals: Record<string, unknown>) {
  const sensors: Record<string, unknown> = {
    temp_mash_tank: machine.sensors.tempMashC,
    temp_boil_tank: machine.sensors.tempBoilC,
    weight_boil_tank: machine.sensors.boilVolumeL,
    water_volume: machine.sensors.boilVolumeL,
    mash_pump_tacho: machine.sensors.mashPumpTacho,
    boil_pump_tacho: machine.sensors.boilPumpTacho
  };
  return definitions.map((definition) => {
    const raw = definition.sensor ? sensors[definition.sensor] : definition.global ? globals[definition.global] : undefined;
    const value = typeof raw === 'number'
      ? (Number.isInteger(raw) ? String(raw) : raw.toFixed(1))
      : raw === undefined || raw === null ? '—' : String(raw);
    return { label: definition.label, value, ...(definition.unit ? { unit: definition.unit } : {}) };
  });
}

export function buildSimulationPlan(graph: BrewGraph, recipe: Recipe, procedures: Record<string, ProcedureDocument>, selections: Record<string, string> = {}): SimulationPlan {
  const steps: SimulationStep[] = [];
  const globals = recipeGlobals(recipe);
  const nodeFor = (procedure: string) => graph.nodes.find((node) => node.procedure === procedure)?.id || procedure;
  const add = (procedure: string, stateId: string, durationS: number, targets: SensorTargets = {}, options: { nodeIds?: string[]; extraActions?: Record<string, unknown>[]; extraOnExit?: Record<string, unknown>[]; ignoreInput?: boolean } = {}) => {
    const procedureDoc = procedures[procedure];
    const state = procedureDoc?.states.find((candidate) => candidate.id === stateId);
    const nodeIds = options.nodeIds || [nodeFor(procedure)];
    const node = graph.nodes.find((candidate) => candidate.id === nodeIds[0]);
    const screen = notification(state, state?.description || stateId.replace(/_/g, ' '));
    const input = options.ignoreInput ? undefined : userInput(procedure, stateId, state);
    steps.push({
      id: `${procedure}:${stateId}:${steps.length}`, nodeIds, procedure, state: stateId,
      title: node?.label || procedure.replace(/_/g, ' '),
      message: screen.message, footerMessage: screen.footerMessage, readouts: screen.readouts,
      ...(input ? { waitForInput: input } : {}),
      durationS: Math.max(1, durationS), actions: [...(state?.actions || []), ...(options.extraActions || [])],
      onExit: [...(state?.on_exit || []), ...(options.extraOnExit || [])], targets
    });
  };

  for (const state of ['review_recipe', 'load_fermentables', 'load_hop_cages', 'confirm_machine_ready']) add('prepare_brew', state, 1);
  add('fill_mash_water', 'prompt_water_fill_method', 1);
  if (selections['fill_mash_water:prompt_water_fill_method'] === 'manual') {
    add('fill_mash_water', 'fill_manually', 1, { boilVolumeL: recipe.water.mash_volume_L });
  } else {
    add('fill_mash_water', 'fill_automatic', Math.max(180, recipe.water.mash_volume_L * 20), { boilVolumeL: recipe.water.mash_volume_L });
  }
  add('fill_mash_water', 'wait_for_user_to_continue', 1);
  add('heat_mash_water', 'check_water_level', 10, {}, { ignoreInput: true });
  add('heat_mash_water', 'heat_mash_water', Math.max(600, (recipe.mash.mash_in_temperature_C - 20) * 25), { tempBoilC: recipe.mash.mash_in_temperature_C });
  add('transfer_water_to_mash', 'prepare_transfer', 15);
  add('transfer_water_to_mash', 'pump_until_current_drop', 180, { boilVolumeL: 1, mashVolumeL: recipe.water.mash_volume_L - 1 });
  add('transfer_water_to_mash', 'settle_water', 10);
  add('transfer_water_to_mash', 'finish_transfer', 5);
  add('fill_sparge_water', 'prompt_water_fill_method', 1);
  if (selections['fill_sparge_water:prompt_water_fill_method'] === 'manual') {
    add('fill_sparge_water', 'fill_manually', 1, { boilVolumeL: recipe.water.sparge_volume_L });
  } else {
    add('fill_sparge_water', 'fill_automatic', Math.max(180, recipe.water.sparge_volume_L * 20), { boilVolumeL: recipe.water.sparge_volume_L });
  }
  add('fill_sparge_water', 'wait_for_user_to_continue', 1);

  const mashNode = nodeFor('two_rest_mashing');
  const heatNode = nodeFor('heat_sparge_water');
  const concurrent = procedures.heat_sparge_water?.states.find((state) => state.id === 'heat_and_circulate');
  const parallel = { nodeIds: [mashNode, heatNode], extraActions: concurrent?.actions || [] };
  add('two_rest_mashing', 'check_mash_setup', 30, { tempMashC: recipe.mash.mash_in_temperature_C }, parallel);
  add('two_rest_mashing', 'start_mash_recirculation', 30, {}, parallel);
  add('two_rest_mashing', 'heat_to_rest_1', 300, { tempMashC: globals.mash_rest_1_temperature_C, tempBoilC: globals.sparge_target_temperature_C }, parallel);
  add('two_rest_mashing', 'rest_1_hold', globals.mash_rest_1_duration_min * 60, {}, parallel);
  add('two_rest_mashing', 'heat_to_rest_2', 240, { tempMashC: globals.mash_rest_2_temperature_C }, parallel);
  add('two_rest_mashing', 'rest_2_hold', globals.mash_rest_2_duration_min * 60, {}, { ...parallel, extraOnExit: concurrent?.on_exit || [] });
  add('two_rest_mashing', 'finish_mashing', 5);

  add('sparging', 'heat_mash_to_sparge_temperature', 300, { tempMashC: globals.sparge_target_temperature_C });
  add('sparging', 'initialize_sparge_cycles', 5);
  const cycles = recipe.sparge?.cycle_count ?? 5;
  for (let cycle = 1; cycle <= cycles; cycle += 1) {
    add('sparging', 'drain_mash_to_boil', 120, { boilVolumeL: recipe.batch_volume_L, mashVolumeL: 1 });
    if (cycle < cycles) {
      add('sparging', 'settle_before_return', 4);
      add('sparging', 'return_twenty_liters_to_mash', 120, { boilVolumeL: Math.max(5, recipe.batch_volume_L - 20), mashVolumeL: Math.min(20, recipe.batch_volume_L - 1) });
      add('sparging', 'settle_before_next_cycle', 10);
    }
  }
  add('sparging', 'finish_sparging', 5, { boilVolumeL: recipe.batch_volume_L, mashVolumeL: 1 });
  add('boiling', 'heat_wort_to_boil', 1200, { tempBoilC: recipe.boil.target_temperature_C });
  const firstHop = Math.max(...recipe.boil.hop_additions.map((hop) => hop.minutes_remaining));
  add('boiling', 'initial_unhopped_boil', Math.max(1, recipe.boil.duration_min - firstHop) * 60);
  const hopTimes = [globals.hop_cage_1_minutes_remaining, globals.hop_cage_2_minutes_remaining, globals.hop_cage_3_minutes_remaining, globals.hop_cage_4_minutes_remaining, 0];
  for (let cage = 1; cage <= 4; cage += 1) add('hopping', `hop_stage_${cage}`, Math.max(1, hopTimes[cage - 1] - hopTimes[cage]) * 60);
  add('hopping', 'finish_hop_session', 5);
  add('cooling', 'cool_wort', 1200, { tempBoilC: recipe.cooling.target_temperature_C });
  add('sedimentation', 'settle_cooled_wort', recipe.sedimentation.duration_min * 60);
  add('transfer_to_fermenter', 'confirm_fermenter_hoses', 1);
  add('transfer_to_fermenter', 'empty_hop_cages', 120);
  add('transfer_to_fermenter', 'transfer_wort', 300, { boilVolumeL: 0.5 });
  add('transfer_to_fermenter', 'finish_transfer', 5);
  return { steps, totalSeconds: steps.reduce((sum, step) => sum + step.durationS, 0) };
}

export function simulationSnapshot(plan: SimulationPlan, recipe: Recipe, elapsedSeconds: number): SimulationSnapshot {
  const elapsed = Math.max(0, Math.min(elapsedSeconds, plan.totalSeconds));
  const globals = recipeGlobals(recipe);
  const machine = createIdleMachineStatus();
  let cursor = 0;
  let activeIndex = Math.max(0, plan.steps.length - 1);
  let currentStartSensors = { ...machine.sensors };
  for (let index = 0; index < plan.steps.length; index += 1) {
    const step = plan.steps[index];
    const end = cursor + step.durationS;
    applyActions(machine, step.actions, globals);
    if (elapsed < end) {
      activeIndex = index;
      const fraction = Math.max(0, Math.min(1, (elapsed - cursor) / step.durationS));
      for (const [key, target] of Object.entries(step.targets)) {
        const sensorKey = key as keyof SensorTargets;
        const start = Number(currentStartSensors[sensorKey] ?? 0);
        (machine.sensors as unknown as Record<string, number>)[sensorKey] = start + (Number(target) - start) * fraction;
      }
      break;
    }
    Object.assign(machine.sensors, step.targets);
    applyActions(machine, step.onExit, globals);
    currentStartSensors = { ...machine.sensors };
    cursor = end;
  }
  machine.sensors.systemWeightKg = machine.sensors.boilVolumeL + machine.sensors.mashVolumeL;
  machine.sensors.mashPumpTacho = machine.pumps.mash_pump ? 220 : 0;
  machine.sensors.boilPumpTacho = machine.pumps.boil_pump ? 220 : 0;
  for (const heater of Object.values(machine.heaters)) if (heater.active && heater.targetC !== null) heater.powerPercent = 65;
  const activeStep = plan.steps[activeIndex];
  const complete = elapsed >= plan.totalSeconds;
  const completedNodeIds = complete
    ? [...new Set(plan.steps.flatMap((step) => step.nodeIds))]
    : [...new Set(plan.steps.filter((step, index) => index < activeIndex).flatMap((step) => step.nodeIds))]
      .filter((node) => !plan.steps.slice(activeIndex).some((step) => step.nodeIds.includes(node)));
  const stepStart = plan.steps.slice(0, activeIndex).reduce((sum, step) => sum + step.durationS, 0);
  const stepProgress = Math.max(0, Math.min(100, ((elapsed - stepStart) / activeStep.durationS) * 100));
  const waiting = !complete && Boolean(activeStep.waitForInput);
  return {
    elapsedSeconds: elapsed, totalSeconds: plan.totalSeconds,
    progress: plan.totalSeconds ? elapsed / plan.totalSeconds * 100 : 0,
    activeNodeIds: complete ? [] : activeStep.nodeIds, completedNodeIds, activeStep,
    session: {
      graph_id: 'beer_brewing', active_node: activeStep.nodeIds[0], active_procedure: activeStep.procedure,
      active_state: activeStep.state, status: complete ? 'complete' : waiting ? 'waiting_for_input' : 'running',
      screen: {
        title: complete ? 'Brew complete' : activeStep.title,
        message: complete ? 'The simulated brewing process has completed.' : activeStep.message,
        footer_message: complete ? '' : activeStep.footerMessage,
        status: complete ? 'complete' : waiting ? 'waiting_for_input' : 'running',
        readouts: complete ? [] : resolvedReadouts(activeStep.readouts, machine, globals),
        choices: waiting ? activeStep.waitForInput!.options.map((value) => ({ value, label: value.replace(/_/g, ' ').toUpperCase() })) : [],
        progress: complete ? 100 : waiting ? null : stepProgress,
        allowed_controls: complete ? ['reset'] : ['pause', 'abort']
      }
    }, machine
  };
}
