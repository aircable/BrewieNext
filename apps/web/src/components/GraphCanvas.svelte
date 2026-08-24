<script lang="ts">
  import { SvelteFlow, Background, Controls, MiniMap, MarkerType } from '@xyflow/svelte';
  import '@xyflow/svelte/dist/style.css';
  import type { BrewGraph } from '../lib/model';

  export let graph: BrewGraph;
  export let activeNode = '';
  export let activeNodes: string[] = activeNode ? [activeNode] : [];
  export let completedNodes: string[] = [];
  export let onSelect: (id: string) => void;

  let flowNodes: any[] = [];
  let flowEdges: any[] = [];
  let graphHeight = 620;
  $: legacyRenderer = typeof window !== 'undefined' && window.location.search.indexOf('legacy=1') !== -1;

  $: flowNodes = graph.nodes.map((node) => ({
    id: node.id,
    position: node.position,
    data: { label: node.label, procedure: node.procedure },
    type: 'default',
    class: [activeNodes.includes(node.id) || node.id === activeNode ? 'active-node' : '', completedNodes.includes(node.id) ? 'completed-node' : '', node.parallel_group ? 'parallel-node' : ''].filter(Boolean).join(' ')
  }));

  $: graphHeight = Math.max(620, ...graph.nodes.map((node) => node.position.y + 150));

  $: flowEdges = graph.edges.map((edge) => ({
    ...edge,
    markerEnd: { type: MarkerType.ArrowClosed },
    label: edge.label || edge.choice_value || ''
  }));

  function nodeClick({ node }: { node: { id: string } }) {
    onSelect(node.id);
  }
</script>

<div class="graph-canvas" style={`height:${graphHeight}px`}>
  <div class="graph-meta">{flowNodes.length} procedures · ordered workflow</div>
  {#if legacyRenderer}
    <div class="legacy-graph" aria-label="Brewing procedure nodes">
      {#each flowNodes as node}
        <button class:legacy-active={activeNodes.includes(node.id) || node.id === activeNode} class:legacy-complete={completedNodes.includes(node.id)} on:click={() => onSelect(node.id)}>
          <strong>{node.data.label}</strong>
          <small>{node.data.procedure}</small>
        </button>
      {/each}
    </div>
  {:else}
    <SvelteFlow bind:nodes={flowNodes} bind:edges={flowEdges} fitView onnodeclick={nodeClick}>
      <Background gap={24} size={1} />
      <Controls />
      <MiniMap />
    </SvelteFlow>
  {/if}
</div>
