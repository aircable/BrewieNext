<script lang="ts">
  import { onMount } from 'svelte';
  import GraphCanvas from './components/GraphCanvas.svelte';
  import BrewieScreen from './components/BrewieScreen.svelte';
  import RecipeEditor from './components/RecipeEditor.svelte';
  import StateInspector from './components/StateInspector.svelte';
  import ProcedureStateGraph from './components/ProcedureStateGraph.svelte';
  import ProgramCatalog from './components/ProgramCatalog.svelte';
  import ProgramScreen from './components/ProgramScreen.svelte';
  import { addWorkflowStep, controlRuntime, createProcedure, loadGraph, loadMachineStatus, loadProcedure, loadPrograms, loadRecipe, loadRecipes, loadRuntimeStatus, navigateRuntime, provideRuntimeInput, removeWorkflowStep, resolveGraph, saveProcedure, saveRecipe, sendMachineCommand, startRuntime, updateWorkflowStep, validateDraft, validateRecipe, type RuntimeStatus } from './lib/api';
  import { graph, session, selectedNode, selectedProcedure, editorMode, notice } from './lib/store';
  import { buildSimulationPlan, createIdleMachineStatus, type SimulationPlan } from './lib/simulation';
  import type { GraphNode, ProcedureDocument, ProgramSummary, Recipe, RecipeSummary } from './lib/model';

  let currentGraph = $graph;
  let currentSession = $session;
  let currentNode = $selectedNode;
  let currentProcedure = $selectedProcedure;
  let mode = $editorMode;
  let message = $notice;
  let selectedState = '';
  let programs: ProgramSummary[] = [];
  let recipes: RecipeSummary[] = [];
  let currentRecipe: Recipe | null = null;
  let procedureDirty = false;
  let nodeDirty = false;
  let recipeDirty = false;
  let simulationPlan: SimulationPlan | null = null;
  let runtimeSnapshot: RuntimeStatus | null = null;
  let machineStatus = createIdleMachineStatus();
  let simulationElapsed = 0;
  let simulationSpeed = 60;
  let simulationRunning = false;
  const kioskMode = typeof window !== 'undefined' && window.location.search.indexOf('kiosk=1') !== -1;
  const requestedRuntimeView = typeof window !== 'undefined' && window.location.search.indexOf('view=runtime') !== -1;

  if (kioskMode && typeof document !== 'undefined') document.body.classList.add('kiosk-body');

  const unsubscribe = [
    graph.subscribe((value) => (currentGraph = value)),
    session.subscribe((value) => (currentSession = value)),
    selectedNode.subscribe((value) => (currentNode = value)),
    selectedProcedure.subscribe((value) => (currentProcedure = value)),
    editorMode.subscribe((value) => (mode = value)),
    notice.subscribe((value) => (message = value))
  ];

  onMount(() => {
    const warnBeforeUnload = (event: BeforeUnloadEvent) => {
      if (!procedureDirty && !nodeDirty && !recipeDirty) return;
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', warnBeforeUnload);
    const refreshRuntime = async () => {
      const status = await loadRuntimeStatus();
      if (status) applyRuntimeSnapshot(status);
      else {
        const machine = await loadMachineStatus();
        if (machine) machineStatus = machine;
        setIdleRuntimeScreen();
      }
    };
    refreshRuntime();
    const runtimeTimer = window.setInterval(refreshRuntime, 300);
    debug('App mounted; loading graph');
    loadGraph()
      .then((value) => {
        graph.set(value);
        debug(`Workflow loaded: ${value.nodes.length} procedures`);
      })
      .catch((error) => debug(`GRAPH ERROR: ${error instanceof Error ? error.message : String(error)}`));
    loadPrograms()
      .then((value) => (programs = value))
      .catch((error) => debug(`PROGRAM ERROR: ${error instanceof Error ? error.message : String(error)}`));
    const refreshProgramCatalog = async () => {
      try {
        programs = await loadPrograms(false);
        window.clearInterval(programTimer);
        debug(`Program catalog connected: ${programs.length} programs`);
      } catch (error) {
        debug(`PROGRAM RETRY: ${error instanceof Error ? error.message : String(error)}`);
      }
    };
    const programTimer = window.setInterval(refreshProgramCatalog, 2000);
    loadRecipes()
      .then(async (value) => {
        recipes = value;
        if (value.length) currentRecipe = await loadRecipe(value[0].id);
        if (requestedRuntimeView) {
          editorMode.set('runtime');
          await prepareSimulation();
        }
      })
      .catch((error) => debug(`RECIPE ERROR: ${error instanceof Error ? error.message : String(error)}`));
    return () => {
      window.removeEventListener('beforeunload', warnBeforeUnload);
      window.clearInterval(runtimeTimer);
      window.clearInterval(programTimer);
      unsubscribe.forEach((stop) => stop());
    };
  });

  function allowNavigation() {
    if (!procedureDirty && !nodeDirty && !recipeDirty) return true;
    if (!window.confirm('You have unsaved changes. Discard them and leave this page?')) return false;
    procedureDirty = false;
    nodeDirty = false;
    recipeDirty = false;
    return true;
  }

  async function selectNode(id: string) {
    if (!allowNavigation()) return;
    const node = currentGraph.nodes.find((candidate) => candidate.id === id);
    if (!node) return;
    selectedNode.set(node);
    selectedProcedure.set(await loadProcedure(node.procedure));
    selectedState = '';
    editorMode.set('procedure');
  }

  async function showRuntime() {
    if (mode !== 'runtime' && !allowNavigation()) return;
    const program = programs.find((candidate) => candidate.workflow === currentGraph.name);
    if (program?.status !== 'available') {
      message = `${program?.label || currentGraph.name} is still in design and cannot run yet.`;
      return;
    }
    editorMode.set('runtime');
    if (!simulationPlan) await prepareSimulation();
  }
  function showPrograms() { if (mode === 'programs' || allowNavigation()) editorMode.set('programs'); }
  function showOverview() { if (mode === 'overview' || allowNavigation()) editorMode.set('overview'); }
  function showRecipes() { if (mode === 'recipes' || allowNavigation()) editorMode.set('recipes'); }
  async function openProgram(program: ProgramSummary) {
    if (!allowNavigation()) return;
    if (!program.workflow) {
      message = `${program.label} does not have a workflow document yet.`;
      return;
    }
    try {
      const loadedGraph = await loadGraph(program.workflow);
      const firstNode = loadedGraph.nodes[0];
      const [loadedRecipe, loadedProcedure] = await Promise.all([
        loadedGraph.default_recipe ? loadRecipe(loadedGraph.default_recipe) : Promise.resolve(null),
        firstNode ? loadProcedure(firstNode.procedure) : Promise.resolve(null)
      ]);
      graph.set(loadedGraph);
      if (loadedRecipe) {
        currentRecipe = loadedRecipe;
        recipeDirty = false;
      }
      if (firstNode) {
        selectedNode.set(firstNode);
        selectedProcedure.set(loadedProcedure);
      } else {
        selectedProcedure.set(null);
      }
      selectedState = '';
      nodeDirty = false;
      simulationPlan = null;
      editorMode.set('overview');
      message = program.status === 'available'
        ? `Opened ${program.label}.`
        : `Opened ${program.label} draft. Add its first procedure to begin designing it.`;
    } catch (error) {
      message = `Program load failed: ${error instanceof Error ? error.message : 'unknown error'}`;
    }
  }
  async function selectRecipe(id: string) {
    if (currentRecipe?.id === id || !allowNavigation()) return;
    try {
      currentRecipe = await loadRecipe(id);
      recipeDirty = false;
      message = `Loaded recipe ${currentRecipe.name}.`;
    } catch (error) {
      message = `Recipe load failed: ${error instanceof Error ? error.message : 'unknown error'}`;
    }
  }
  function editRecipe(recipe: Recipe) {
    currentRecipe = recipe;
    recipeDirty = true;
    message = `Recipe ${recipe.name} has unsaved changes.`;
  }
  function newRecipe() {
    if (!allowNavigation()) return;
    let suffix = 1;
    let id = 'new_recipe';
    while (recipes.some((recipe) => recipe.id === id)) id = `new_recipe_${++suffix}`;
    currentRecipe = {
      schema_version: 1,
      id,
      name: 'New recipe',
      description: '',
      style: '',
      batch_volume_L: 20,
      water: { mash_volume_L: 15, sparge_volume_L: 10 },
      fermentables: [{ name: 'Pale malt', amount_kg: 4, bag: 1 }],
      mash: {
        mash_in_temperature_C: 63,
        steps: [
          { name: 'Saccharification', target_temperature_C: 67, duration_min: 60 },
          { name: 'Mash out', target_temperature_C: 76, duration_min: 10 }
        ]
      },
      sparge: { target_temperature_C: 78, cycle_count: 5 },
      boil: {
        duration_min: 60,
        target_temperature_C: 100,
        hop_additions: [60, 30, 10, 5].map((minutes_remaining, index) => ({
          cage: index + 1,
          hop: '',
          amount_g: 0,
          minutes_remaining
        }))
      },
      cooling: { target_temperature_C: 20 },
      sedimentation: { duration_min: 20 },
      yeasts: []
    };
    recipeDirty = true;
    message = 'New recipe draft created. Change its ID and details before saving.';
  }
  async function validateCurrentRecipe() {
    if (!currentRecipe) return;
    try {
      const result = await validateRecipe(currentRecipe);
      message = result.valid ? 'Recipe is valid.' : `Recipe has ${result.errors.length} validation error(s): ${result.errors[0] || ''}`;
    } catch (error) {
      message = `Recipe validation failed: ${error instanceof Error ? error.message : 'unknown error'}`;
    }
  }
  async function saveCurrentRecipe() {
    if (!currentRecipe) return;
    try {
      const result = await validateRecipe(currentRecipe);
      if (!result.valid) {
        message = `Recipe not saved: ${result.errors[0] || 'validation failed'}`;
        return;
      }
      await saveRecipe(currentRecipe);
      recipes = await loadRecipes();
      recipeDirty = false;
      message = `Saved recipe ${currentRecipe.name}.`;
    } catch (error) {
      message = `Recipe save failed: ${error instanceof Error ? error.message : 'unknown error'}`;
    }
  }
  async function useCurrentRecipe() {
    if (!currentRecipe) return;
    try {
      const result = await validateRecipe(currentRecipe);
      if (!result.valid) {
        message = `Cannot use recipe: ${result.errors[0] || 'validation failed'}`;
        return;
      }
      await saveRecipe(currentRecipe);
      recipeDirty = false;
      const resolved = await resolveGraph(currentRecipe.id, currentGraph.name);
      graph.set(resolved);
      simulationPlan = null;
      runtimeSnapshot = null;
      editorMode.set('overview');
      message = `Brew workflow prepared with ${currentRecipe.name}.`;
    } catch (error) {
      message = `Preparing brew failed: ${error instanceof Error ? error.message : 'unknown error'}`;
    }
  }
  function debug(message: string) {
    const panel = document.getElementById('boot-debug');
    if (panel && window.location.search.indexOf('debug=1') !== -1) panel.textContent += `\n${message}`;
  }
  function formatSimulationTime(seconds: number) {
    const total = Math.max(0, Math.round(seconds));
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const secs = total % 60;
    return hours ? `${hours}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}` : `${minutes}:${String(secs).padStart(2, '0')}`;
  }
  function simulationSegments() {
    if (!simulationPlan) return [];
    const segments: { key: string; label: string; start: number; end: number }[] = [];
    let cursor = 0;
    for (const step of simulationPlan.steps) {
      const key = step.nodeIds.join('+');
      const end = cursor + step.durationS;
      const previous = segments[segments.length - 1];
      if (previous?.key === key) previous.end = end;
      else {
        const label = step.nodeIds.map((id) => currentGraph.nodes.find((node) => node.id === id)?.label || id).join(' + ');
        segments.push({ key, label, start: cursor, end });
      }
      cursor = end;
    }
    return segments;
  }
  function applyRuntimeSnapshot(snapshot: RuntimeStatus) {
    runtimeSnapshot = snapshot;
    simulationElapsed = snapshot.elapsed_s;
    simulationSpeed = snapshot.speed;
    simulationRunning = snapshot.status === 'running';
    machineStatus = snapshot.machine;
    const visibleStatus = snapshot.status === 'aborted' ? 'error' : snapshot.status;
    currentSession = {
      graph_id: snapshot.workflow,
      active_node: snapshot.active_nodes[0] || '',
      active_procedure: snapshot.active_procedure || '',
      active_state: snapshot.active_state || '',
      status: visibleStatus,
      screen: { ...snapshot.screen, status: visibleStatus }
    };
    session.set(currentSession);
  }

  function setIdleRuntimeScreen() {
    if (runtimeSnapshot) return;
    currentSession = {
      graph_id: currentGraph.name,
      active_node: '',
      active_procedure: '',
      active_state: '',
      status: 'idle',
      screen: {
        title: 'BrewieNext ready',
        message: kioskMode ? 'Start a brew from the browser or press START for hardware mode.' : 'Select a recipe and start simulation or hardware mode.',
        footer_message: '',
        status: 'idle',
        readouts: [],
        choices: [],
        progress: null,
        allowed_controls: ['start']
      }
    };
    session.set(currentSession);
  }

  async function prepareSimulation() {
    if (!currentRecipe) {
      message = 'Load or select a recipe before starting the simulation.';
      return;
    }
    try {
      const names = [...new Set(currentGraph.nodes.map((node) => node.procedure))];
      const loaded = await Promise.all(names.map(async (name) => [name, await loadProcedure(name)] as const));
      const procedures = Object.fromEntries(loaded.filter((entry): entry is readonly [string, ProcedureDocument] => entry[1] !== null));
      simulationPlan = buildSimulationPlan(currentGraph, currentRecipe, procedures);
      const existing = await loadRuntimeStatus();
      if (existing) {
        applyRuntimeSnapshot(existing);
        message = `${existing.mode === 'hardware' ? 'Hardware brew' : 'Simulation'} session restored.`;
      } else {
        simulationElapsed = 0;
        simulationRunning = false;
        message = `Runner ready: estimated ${formatSimulationTime(simulationPlan.totalSeconds)} at 1×, ${formatSimulationTime(simulationPlan.totalSeconds / 60)} at 60×.`;
      }
    } catch (error) {
      message = `Simulation setup failed: ${error instanceof Error ? error.message : 'unknown error'}`;
    }
  }
  async function startSimulation() {
    if (!currentRecipe) return;
    try {
      if (runtimeSnapshot && !['complete', 'error', 'aborted'].includes(runtimeSnapshot.status)) {
        if (runtimeSnapshot.status === 'paused') applyRuntimeSnapshot(await controlRuntime('resume'));
        else if (runtimeSnapshot.status === 'waiting_for_input') message = 'The runner is waiting for the choice shown on the local screen.';
        return;
      }
      applyRuntimeSnapshot(await startRuntime({ mode: 'simulation', recipe_id: currentRecipe.id, workflow: currentGraph.name, speed: simulationSpeed }));
      message = `Authoritative simulation running at ${simulationSpeed}×.`;
    } catch (error) {
      message = `Simulation start failed: ${error instanceof Error ? error.message : String(error)}`;
    }
  }
  async function pauseSimulation() {
    try {
      applyRuntimeSnapshot(await controlRuntime('pause'));
      message = `Runner paused at ${formatSimulationTime(simulationElapsed)}.`;
    } catch (error) {
      message = `Pause failed: ${error instanceof Error ? error.message : String(error)}`;
    }
  }
  async function resetSimulation() {
    if (!currentRecipe) return;
    if (runtimeSnapshot?.mode === 'hardware' && !['complete', 'error', 'aborted'].includes(runtimeSnapshot.status)) {
      message = 'Abort the hardware brew explicitly before starting a simulation.';
      return;
    }
    try {
      if (runtimeSnapshot && !['complete', 'error', 'aborted'].includes(runtimeSnapshot.status)) await controlRuntime('abort');
      applyRuntimeSnapshot(await startRuntime({ mode: 'simulation', recipe_id: currentRecipe.id, workflow: currentGraph.name, speed: simulationSpeed }));
      message = 'Simulation restarted from the first procedure.';
    } catch (error) {
      message = `Simulation reset failed: ${error instanceof Error ? error.message : String(error)}`;
    }
  }
  async function startHardwareRuntime() {
    if (!currentRecipe || !window.confirm('Start a REAL brew? The runner will issue P999, then execute procedure actions on the AVR.')) return;
    try {
      if (runtimeSnapshot && !['complete', 'error', 'aborted'].includes(runtimeSnapshot.status)) {
        if (!window.confirm('An active runner session exists. Abort and replace it?')) return;
        await controlRuntime('abort');
      }
      applyRuntimeSnapshot(await startRuntime({ mode: 'hardware', recipe_id: currentRecipe.id, workflow: currentGraph.name, confirm_hardware: true }));
      message = 'Hardware runner started at 1×; AVR commands are live.';
    } catch (error) {
      message = `Hardware start failed: ${error instanceof Error ? error.message : String(error)}`;
    }
  }
  async function setRuntimeSpeed() {
    if (runtimeSnapshot?.mode !== 'simulation' || ['complete', 'error', 'aborted'].includes(runtimeSnapshot.status)) return;
    try {
      applyRuntimeSnapshot(await controlRuntime('set_speed', simulationSpeed));
      message = `Simulation speed changed to ${simulationSpeed}×.`;
    } catch (error) {
      message = `Speed change failed: ${error instanceof Error ? error.message : String(error)}`;
    }
  }
  async function simulationControl(control: string) {
    try {
      if (control === 'start') {
        if (kioskMode) await startHardwareRuntime();
        else await startSimulation();
      }
      else if (control.startsWith('input:')) {
        const active = runtimeSnapshot?.procedures.find((procedure) => procedure.input);
        if (!active?.input) throw new Error('No active procedure is waiting for input');
        applyRuntimeSnapshot(await provideRuntimeInput(active.input.key, control.slice('input:'.length), active.procedure));
        message = `Accepted ${control.slice('input:'.length)}; runner continuing.`;
      } else if (control === 'pause') {
        if (runtimeSnapshot?.status === 'paused') await startSimulation();
        else await pauseSimulation();
      } else if (control === 'reset') {
        if (kioskMode || runtimeSnapshot?.mode === 'hardware') await startHardwareRuntime();
        else await resetSimulation();
      }
      else if (control === 'previous_procedure' || control === 'next_procedure') {
        applyRuntimeSnapshot(await navigateRuntime(control === 'previous_procedure' ? 'previous' : 'next'));
        message = `Runner moved to ${runtimeSnapshot?.active_procedure?.replace(/_/g, ' ')}.`;
      } else if (control === 'programs') {
        showPrograms();
      } else if (control === 'abort') {
        if (!window.confirm(`Abort the ${runtimeSnapshot?.mode || 'active'} runner and close all outputs?`)) return;
        applyRuntimeSnapshot(await controlRuntime('abort'));
        message = 'Runner aborted; all modeled outputs were closed.';
      } else if (control === 'close_all') {
        machineStatus = await sendMachineCommand({ command: 'close_all' });
        message = runtimeSnapshot?.mode === 'hardware' ? 'AVR acknowledged P999.' : 'Simulation outputs closed by user intervention.';
      } else if (control === 'reset_level') {
        machineStatus = await sendMachineCommand({ command: 'reset_level' });
        message = 'Level reading reset by user intervention.';
      } else if (control.startsWith('heater_target:')) {
        const [, id, rawTarget] = control.split(':');
        machineStatus = await sendMachineCommand({ device: id, target_C: Number(rawTarget) });
        message = `${id.replace(/_/g, ' ')} target changed by user intervention.`;
      } else if (control.startsWith('toggle:')) {
        const id = control.slice('toggle:'.length);
        machineStatus = await sendMachineCommand({ device: id, action: 'toggle' });
        message = `${id.replace(/_/g, ' ')} changed by user intervention.`;
      }
    } catch (error) {
      message = `Runner command failed: ${error instanceof Error ? error.message : String(error)}`;
    }
  }
  function editNodeMetadata(field: 'label' | 'description', value: string) {
    const nodes = currentGraph.nodes.map((node) => node.id === currentNode.id ? { ...node, [field]: value } : node);
    const updatedGraph = { ...currentGraph, nodes };
    graph.set(updatedGraph);
    selectedNode.set(nodes.find((node) => node.id === currentNode.id) || currentNode);
    nodeDirty = true;
    message = `Workflow node changed. Save the node to update ${currentGraph.name}.`;
  }
  async function saveNodeMetadata() {
    if (!currentNode || !nodeDirty) return;
    try {
      const updated = await updateWorkflowStep(currentGraph.name, currentNode.id, {
        label: currentNode.label,
        description: currentNode.description || ''
      });
      graph.set(updated);
      selectedNode.set(updated.nodes.find((node) => node.id === currentNode.id) || currentNode);
      nodeDirty = false;
      message = `Saved workflow node ${currentNode.label}.`;
    } catch (error) {
      message = `Save node failed: ${error instanceof Error ? error.message : 'unknown error'}`;
    }
  }
  async function addProcedureToWorkflow(after?: string) {
    if (!allowNavigation()) return;
    const requested = window.prompt('Procedure ID (lowercase snake_case):', 'new_procedure');
    if (requested === null) return;
    const procedureId = requested.trim();
    if (!/^[a-z][a-z0-9_]*$/.test(procedureId)) {
      message = 'Procedure IDs must use lowercase snake_case.';
      return;
    }
    const defaultLabel = procedureId.replace(/_/g, ' ');
    const requestedLabel = window.prompt('Workflow label:', defaultLabel);
    if (requestedLabel === null) return;
    const label = requestedLabel.trim() || defaultLabel;
    try {
      const wasEmpty = currentGraph.nodes.length === 0;
      const existing = await loadProcedure(procedureId);
      if (!existing) await createProcedure(procedureId, label);
      const updated = await addWorkflowStep(currentGraph.name, {
        id: procedureId,
        label,
        procedure: procedureId,
        ...(after ? { after } : {})
      });
      graph.set(updated);
      if (wasEmpty && updated.nodes[0]) {
        selectedNode.set(updated.nodes[0]);
        selectedProcedure.set(await loadProcedure(updated.nodes[0].procedure));
      }
      message = `Added ${procedureId}${after ? ` after ${after}` : ' at the end of the workflow'}.`;
    } catch (error) {
      message = `Add procedure failed: ${error instanceof Error ? error.message : 'unknown error'}`;
    }
  }
  async function removeCurrentWorkflowStep() {
    if (!currentNode || !allowNavigation()) return;
    if (!window.confirm(`Remove ${currentNode.label} from the brew workflow? Its procedure file will be preserved.`)) return;
    try {
      const removedId = currentNode.id;
      const updated = await removeWorkflowStep(currentGraph.name, removedId);
      graph.set(updated);
      selectedProcedure.set(null);
      selectedNode.set(updated.nodes[0]);
      selectedState = '';
      editorMode.set('overview');
      message = `Removed ${removedId} from the workflow; its procedure definition was preserved.`;
    } catch (error) {
      message = `Remove procedure failed: ${error instanceof Error ? error.message : 'unknown error'}`;
    }
  }
  function selectState(id: string) { selectedState = id; message = `Selected state ${id}.`; }
  function renameState(value: string) {
    if (!currentProcedure) return;
    const oldId = selectedState || currentProcedure.start_state;
    const newId = value.trim();
    if (!/^[a-z][a-z0-9_]*$/.test(newId)) {
      message = 'State names must use lowercase snake_case.';
      return;
    }
    if (newId !== oldId && currentProcedure.states.some((state) => state.id === newId)) {
      message = `State ${newId} already exists.`;
      return;
    }
    if (newId === oldId) return;
    const states = currentProcedure.states.map((state) => ({
      ...state,
      id: state.id === oldId ? newId : state.id,
      transitions: state.transitions.map((transition) => ({
        ...transition,
        then: transition.then === oldId ? newId : transition.then,
        ...(transition.default === oldId ? { default: newId } : {})
      }))
    }));
    currentProcedure = {
      ...currentProcedure,
      states,
      start_state: currentProcedure.start_state === oldId ? newId : currentProcedure.start_state,
      error_handler: currentProcedure.error_handler === oldId ? newId : currentProcedure.error_handler
    };
    selectedState = newId;
    selectedProcedure.set(currentProcedure);
    procedureDirty = true;
    message = `Renamed ${oldId} to ${newId}; transition targets were updated.`;
  }
  function reorderState(sourceIndex: number, targetIndex: number) {
    if (!currentProcedure) return;
    if (sourceIndex < 0 || targetIndex < 0 || sourceIndex >= currentProcedure.states.length || targetIndex >= currentProcedure.states.length) return;
    const states = [...currentProcedure.states];
    const [state] = states.splice(sourceIndex, 1);
    states.splice(targetIndex, 0, state);
    currentProcedure = { ...currentProcedure, states };
    selectedProcedure.set(currentProcedure);
    procedureDirty = true;
    message = `Moved ${state.id}; execution still begins at ${currentProcedure.start_state}.`;
  }
  function addState() {
    if (!currentProcedure) return;
    const procedure = currentProcedure;
    const existing = new Set(procedure.states.map((state) => state.id));
    let suffix = 1;
    let id = 'new_state';
    while (existing.has(id)) id = `new_state_${++suffix}`;
    const states = [...procedure.states];
    states.push({
      id,
      description: 'Describe what this state does.',
      actions: [],
      transitions: []
    });
    currentProcedure = { ...procedure, states };
    selectedState = id;
    selectedProcedure.set(currentProcedure);
    procedureDirty = true;
    message = `Added ${id}. Rename it and define its actions and transitions.`;
  }
  function removeState(requestedStateId?: string) {
    if (!currentProcedure) return;
    const activeStateId = selectedState || currentProcedure.start_state;
    const stateId = requestedStateId || selectedState || currentProcedure.start_state;
    if (currentProcedure.states.length <= 1) {
      message = 'A procedure must contain at least one state.';
      return;
    }
    if (!window.confirm(`Remove state ${stateId}? Incoming transitions to it will also be removed.`)) return;
    const index = currentProcedure.states.findIndex((state) => state.id === stateId);
    if (index < 0) return;
    let removedTransitions = 0;
    const states = currentProcedure.states
      .filter((state) => state.id !== stateId)
      .map((state) => ({
        ...state,
        transitions: state.transitions.filter((transition) => {
          const keep = transition.then !== stateId && transition.default !== stateId;
          if (!keep) removedTransitions += 1;
          return keep;
        })
      }));
    const replacement = states[Math.min(index, states.length - 1)].id;
    currentProcedure = {
      ...currentProcedure,
      states,
      start_state: currentProcedure.start_state === stateId ? replacement : currentProcedure.start_state,
      error_handler: currentProcedure.error_handler === stateId ? undefined : currentProcedure.error_handler
    };
    if (activeStateId === stateId) selectedState = replacement;
    selectedProcedure.set(currentProcedure);
    procedureDirty = true;
    message = `Removed ${stateId}${removedTransitions ? ` and ${removedTransitions} incoming transition(s)` : ''}.`;
  }
  function editState(field: string, index: number, value: string) {
    if (!currentProcedure) return;
    const stateId = selectedState || currentProcedure.start_state;
    const states = currentProcedure.states.map((state) => {
      if (state.id !== stateId) return state;
      if (field === 'description') return { ...state, description: value };
      if (field === 'timeout_s') return { ...state, timeout_s: value || undefined };
      if (field === 'action') {
        try {
          const actions = [...state.actions];
          actions[index] = JSON.parse(value);
          return { ...state, actions };
        } catch {
          message = 'Action must contain valid JSON.';
          return state;
        }
      }
      if (field === 'action-add') {
        return { ...state, actions: [...state.actions, { notify_user: { text: 'New action' } }] };
      }
      if (field === 'action-remove') {
        return { ...state, actions: state.actions.filter((_, actionIndex) => actionIndex !== index) };
      }
      if (field === 'on-exit') {
        try {
          const on_exit = [...(state.on_exit || [])];
          on_exit[index] = JSON.parse(value);
          return { ...state, on_exit };
        } catch {
          message = 'On-exit action must contain valid JSON.';
          return state;
        }
      }
      if (field === 'on-exit-add') {
        return {
          ...state,
          on_exit: [...(state.on_exit || []), { set_valve: { valve: 'boil_inlet_valve', state: 'closed' } }]
        };
      }
      if (field === 'on-exit-remove') {
        return { ...state, on_exit: (state.on_exit || []).filter((_, actionIndex) => actionIndex !== index) };
      }
      if (field === 'transition-condition') {
        const transitions = state.transitions.map((transition, transitionIndex) => transitionIndex === index
          ? (value === 'default' ? { ...transition, condition: undefined, default: transition.then } : { ...transition, condition: value, default: undefined })
          : transition);
        return { ...state, transitions };
      }
      if (field === 'transition-target') {
        const transitions = state.transitions.map((transition, transitionIndex) => transitionIndex === index ? { ...transition, then: value } : transition);
        return { ...state, transitions };
      }
      if (field === 'transition-add') {
        return { ...state, transitions: [...state.transitions, { condition: '', then: '' }] };
      }
      if (field === 'transition-remove') {
        return { ...state, transitions: state.transitions.filter((_, transitionIndex) => transitionIndex !== index) };
      }
      if (field === 'transition-move') {
        const targetIndex = Number(value);
        if (!Number.isInteger(targetIndex) || targetIndex < 0 || targetIndex >= state.transitions.length) return state;
        const transitions = [...state.transitions];
        const [transition] = transitions.splice(index, 1);
        transitions.splice(targetIndex, 0, transition);
        return { ...state, transitions };
      }
      return state;
    });
    currentProcedure = { ...currentProcedure, states };
    selectedProcedure.set(currentProcedure);
    procedureDirty = true;
    message = `Draft changed for ${currentProcedure.name}. Validate before saving.`;
  }
  async function validateCurrent() {
    if (!currentProcedure) return;
    const resourceName = currentNode.procedure || currentProcedure.name;
    const result = await validateDraft(resourceName, { ...currentProcedure, name: resourceName });
    message = result.valid ? 'Draft is valid.' : `Draft has ${result.issues.length} validation issue(s).`;
  }
  async function saveCurrent() {
    if (!currentProcedure) return;
    try {
      const resourceName = currentNode.procedure || currentProcedure.name;
      const savedProcedure = { ...currentProcedure, name: resourceName };
      await saveProcedure(resourceName, savedProcedure);
      currentProcedure = savedProcedure;
      selectedProcedure.set(savedProcedure);
      procedureDirty = false;
      message = `Saved ${resourceName}.`;
    } catch (error) {
      message = `Save failed: ${error instanceof Error ? error.message : 'unknown error'}`;
    }
  }
</script>

<svelte:head><title>BrewieNext Procedure Studio</title></svelte:head>

{#if kioskMode}
  <main class="kiosk-layout" aria-label="Brewie hardware display">
    {#if mode === 'programs'}
      <ProgramScreen {programs} onSelect={openProgram} />
    {:else}
      <BrewieScreen session={currentSession} machine={machineStatus} onControl={simulationControl} />
    {/if}
  </main>
{:else}
<div class="app-shell">
  <header class="topbar">
    <div><div class="brand">BREWIE<span>NEXT</span></div><div class="subtitle">Procedure Studio</div></div>
    <nav>
      <button class:active={mode === 'programs'} on:click={showPrograms}>Programs</button>
      <button class:active={mode === 'recipes'} on:click={showRecipes}>Recipes</button>
      <button class:active={mode === 'runtime'} on:click={showRuntime} disabled={programs.find((program) => program.workflow === currentGraph.name)?.status === 'design'}>Live brew</button>
      <span class="connection">● LOCAL</span>
    </nav>
  </header>

  <div class="notice">{message}</div>

  {#if mode === 'programs'}
    <main class="programs-layout">
      <ProgramCatalog {programs} onOpen={openProgram} />
      <aside class="runtime-sidebar program-previews">
        <div class="runtime-popup"><div class="eyebrow">LOCAL BREWIE SCREEN · PROGRAMS</div><ProgramScreen {programs} onSelect={openProgram} /></div>
        <div class="runtime-popup"><div class="eyebrow">BREWMASTER STATUS · READY</div><BrewieScreen session={currentSession} machine={machineStatus} onControl={simulationControl} initialView="machine" /></div>
      </aside>
    </main>
  {:else if mode === 'recipes'}
    <main class="recipe-layout">
      <RecipeEditor recipes={recipes} recipe={currentRecipe} onSelect={selectRecipe} onChange={editRecipe} onValidate={validateCurrentRecipe} onSave={saveCurrentRecipe} onUse={useCurrentRecipe} onNew={newRecipe} />
    </main>
  {:else if mode === 'runtime'}
    <main class="runtime-layout">
      <section class="runtime-graph">
        <div class="section-heading runtime-heading">
          <div><div class="eyebrow">LIVE ORCHESTRATION · {runtimeSnapshot?.status.toUpperCase() || 'READY'}</div><h1>{currentGraph.name}</h1></div>
          <div class="simulation-controls">
            <button class="primary" on:click={simulationRunning ? pauseSimulation : startSimulation}>{simulationRunning ? 'Pause' : runtimeSnapshot?.status === 'paused' ? 'Resume' : runtimeSnapshot?.status === 'waiting_for_input' ? 'Waiting for input' : 'Start simulation'}</button>
            <button on:click={resetSimulation}>Reset</button>
            <label>Speed
              <select bind:value={simulationSpeed} on:change={setRuntimeSpeed} disabled={runtimeSnapshot?.mode === 'hardware'}>
                <option value={1}>1×</option><option value={10}>10×</option><option value={30}>30×</option><option value={60}>60× · 5 h → 5 min</option><option value={120}>120×</option>
              </select>
            </label>
            <button class="danger" on:click={startHardwareRuntime}>Start hardware</button>
            <button on:click={showOverview}>Back to editor</button>
          </div>
        </div>
        <div class="simulation-status">
          <div><strong>{runtimeSnapshot?.active_procedure?.replace(/_/g, ' ') || 'Ready'}</strong><span>{runtimeSnapshot?.active_state || 'Runner not started'}</span></div>
          <div class="simulation-clock"><strong>{formatSimulationTime(simulationElapsed)}</strong><span>/ {formatSimulationTime(simulationPlan?.totalSeconds || 0)}</span></div>
          <div class="simulation-progress"><span style={`width:${runtimeSnapshot ? (runtimeSnapshot.step_index + (runtimeSnapshot.screen.progress || 0) / 100) / Math.max(1, runtimeSnapshot.step_count) * 100 : 0}%`}></span></div>
          <div class="simulation-timeline" aria-label="Simulated brew procedure timing">
            {#each simulationSegments() as segment}
              <span class:active={simulationElapsed >= segment.start && simulationElapsed < segment.end} class:complete={simulationElapsed >= segment.end} style={`width:${simulationPlan ? (segment.end - segment.start) / simulationPlan.totalSeconds * 100 : 0}%`} title={`${segment.label} · ${formatSimulationTime(segment.end - segment.start)}`}></span>
            {/each}
          </div>
        </div>
        <GraphCanvas graph={currentGraph} activeNode={currentSession.active_node} activeNodes={runtimeSnapshot?.active_nodes || [currentSession.active_node]} completedNodes={runtimeSnapshot?.completed_nodes || []} onSelect={selectNode} />
      </section>
      <aside class="runtime-sidebar">
        <div class="runtime-popup"><div class="eyebrow">LOCAL BREWIE SCREEN · {currentSession.active_procedure}</div><BrewieScreen session={currentSession} machine={machineStatus} onControl={simulationControl} /></div>
        <div class="runtime-popup"><div class="eyebrow">BREWMASTER STATUS · {runtimeSnapshot?.mode.toUpperCase() || 'READY'}</div><BrewieScreen session={currentSession} machine={machineStatus} onControl={simulationControl} initialView="machine" /></div>
      </aside>
    </main>
  {:else if mode === 'procedure' && currentProcedure}
    <main class="procedure-layout">
      <section class="workspace">
        <div class="section-heading"><div><button on:click={showOverview}>← Workflow</button><div class="eyebrow">SUBPROCEDURE</div><h1>{currentProcedure.name}</h1></div><div class="workflow-actions"><button on:click={() => addProcedureToWorkflow(currentNode.id)}>+ Procedure after</button><button class="danger" on:click={removeCurrentWorkflowStep}>Remove from workflow</button><button class="primary" on:click={validateCurrent}>Validate procedure</button></div></div>
        <ProcedureStateGraph procedure={currentProcedure} selectedState={selectedState || currentProcedure.start_state} onSelect={selectState} onReorder={reorderState} onAdd={addState} onRemove={removeState} />
      </section>
      <StateInspector node={currentNode} procedure={currentProcedure} selectedStateId={selectedState || currentProcedure.start_state} onStateEdit={editState} onStateRename={renameState} onNodeEdit={editNodeMetadata} onNodeSave={saveNodeMetadata} onValidate={validateCurrent} onSave={saveCurrent} />
    </main>
  {:else}
    <main class="studio-layout">
      <section class="workspace">
        <div class="section-heading"><div><button on:click={showPrograms}>← Programs</button><div class="eyebrow">ORDERED MACHINE WORKFLOW{programs.find((program) => program.workflow === currentGraph.name)?.status === 'design' ? ' · DRAFT' : ''}</div><h1>{currentGraph.description}</h1></div><div class="workflow-actions"><button on:click={() => addProcedureToWorkflow()}>+ Procedure at end</button><button class="primary">Validate workflow</button></div></div>
        <GraphCanvas graph={currentGraph} activeNode={currentSession.active_node} onSelect={selectNode} />
      </section>
      {#if currentGraph.nodes.length}
        <StateInspector node={currentNode} procedure={currentProcedure} selectedStateId={selectedState} onStateEdit={editState} onStateRename={renameState} onNodeEdit={editNodeMetadata} onNodeSave={saveNodeMetadata} onValidate={validateCurrent} onSave={saveCurrent} />
      {:else}
        <aside class="inspector"><div class="eyebrow">EMPTY WORKFLOW</div><h2>Start with a procedure</h2><p class="muted">Use “+ Procedure at end” to create or reuse the first procedure in this program.</p></aside>
      {/if}
    </main>
  {/if}

  <footer><span>Draft / validated before save</span><span>Safety-critical execution remains on the engine</span></footer>
</div>
{/if}
