import { writable } from 'svelte/store';
import { demoGraph, demoSession, type BrewGraph, type BrewSession, type GraphNode, type ProcedureDocument } from './model';

export const graph = writable<BrewGraph>(demoGraph);
export const session = writable<BrewSession>(demoSession);
export const selectedNode = writable<GraphNode>(demoGraph.nodes[1]);
export const selectedProcedure = writable<ProcedureDocument | null>(null);
export const editorMode = writable<'overview' | 'procedure' | 'recipes' | 'runtime'>('overview');
export const notice = writable('Fixture mode: connect the editor backend to load live YAML.');
