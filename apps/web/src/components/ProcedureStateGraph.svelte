<script lang="ts">
  import type { ProcedureDocument } from '../lib/model';
  export let procedure: ProcedureDocument;
  export let selectedState = '';
  export let onSelect: (id: string) => void;
  export let onReorder: (sourceIndex: number, targetIndex: number) => void;
  export let onAdd: () => void;
  export let onRemove: (id: string) => void;

  let draggedStateIndex: number | null = null;

  function startStateDrag(event: DragEvent, index: number) {
    draggedStateIndex = index;
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', String(index));
    }
  }

  function dropState(event: DragEvent, targetIndex: number) {
    event.preventDefault();
    const sourceIndex = draggedStateIndex ?? Number(event.dataTransfer?.getData('text/plain'));
    draggedStateIndex = null;
    if (Number.isInteger(sourceIndex) && sourceIndex !== targetIndex) onReorder(sourceIndex, targetIndex);
  }
</script>

<div class="state-graph" role="list" aria-label="Procedure state graph">
  {#each procedure.states as state, index}
    <div
      class="state-row"
      class:selected={state.id === selectedState}
      class:start={state.id === procedure.start_state}
      class:dragging={draggedStateIndex === index}
      role="listitem"
      draggable={true}
      on:dragstart={(event) => startStateDrag(event, index)}
      on:dragend={() => (draggedStateIndex = null)}
      on:dragover={(event) => event.preventDefault()}
      on:drop={(event) => dropState(event, index)}
    >
      <button class="state-select" title={state.description} on:click={() => onSelect(state.id)}>
        <span class="state-drag" aria-hidden="true">⋮⋮</span>
        <span class="state-number">{index + 1}</span>
        <strong>{state.id}</strong>
        <span class="state-actions">{state.actions.length} actions · {state.transitions.length} exits</span>
      </button>
      <button class="state-delete" disabled={procedure.states.length <= 1} aria-label={`Remove ${state.id}`} title={`Remove ${state.id}`} on:click={() => onRemove(state.id)}>×</button>
    </div>
  {/each}
  <button class="state-add-row" on:click={onAdd}><span aria-hidden="true">＋</span> Add state</button>
</div>
