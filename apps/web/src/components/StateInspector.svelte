<script lang="ts">
  import type { GraphNode, ProcedureDocument } from '../lib/model';
  export let node: GraphNode;
  export let procedure: ProcedureDocument | null = null;
  export let onNodeEdit: (field: 'label' | 'description', value: string) => void;
  export let onNodeSave: () => void;
  export let onValidate: () => void;
  export let onSave: () => void;
  export let selectedStateId = '';
  export let onStateEdit: (field: string, index: number, value: string) => void;
  export let onStateRename: (newId: string) => void;

  let draggedTransitionIndex: number | null = null;

  function startTransitionDrag(event: DragEvent, index: number) {
    draggedTransitionIndex = index;
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', String(index));
    }
  }

  function dropTransition(event: DragEvent, targetIndex: number) {
    event.preventDefault();
    const sourceIndex = draggedTransitionIndex ?? Number(event.dataTransfer?.getData('text/plain'));
    draggedTransitionIndex = null;
    if (Number.isInteger(sourceIndex) && sourceIndex !== targetIndex) {
      onStateEdit('transition-move', sourceIndex, String(targetIndex));
    }
  }

  $: selectedState = procedure?.states.find((state) => state.id === selectedStateId) || procedure?.states[0];
</script>

<aside class="inspector">
  <div class="eyebrow">SELECTED NODE</div>
  <p class="muted">{node.procedure}</p>
  <label>
    Name
    <input value={node.label} on:input={(event) => onNodeEdit('label', (event.currentTarget as HTMLInputElement).value)} />
  </label>
  <label>
    Description
    <textarea value={node.description} on:input={(event) => onNodeEdit('description', (event.currentTarget as HTMLTextAreaElement).value)}></textarea>
  </label>
  <button on:click={onNodeSave}>Save node</button>
  <div class="inspector-section">
    <div class="eyebrow">PROCEDURE</div>
    {#if procedure}
      <p><strong>Start:</strong> {procedure.start_state}</p>
      <p><strong>States:</strong> {procedure.states.length}</p>
      {#if selectedState}
        <div class="state-editor">
          <div class="eyebrow">STATE · {selectedState.id}</div>
          <label>
            State name
            <input value={selectedState.id} on:change={(event) => onStateRename((event.currentTarget as HTMLInputElement).value)} />
          </label>
          <label>
            State description
            <textarea value={selectedState.description} on:input={(event) => onStateEdit('description', -1, (event.currentTarget as HTMLTextAreaElement).value)}></textarea>
          </label>
          <label>
            Timeout
            <input value={selectedState.timeout_s || ''} on:input={(event) => onStateEdit('timeout_s', -1, (event.currentTarget as HTMLInputElement).value)} />
          </label>
          <div class="eyebrow editor-label">ACTIONS</div>
          {#each selectedState.actions as action, index}
            <div class="action-editor-row">
              <textarea class="code-editor" value={JSON.stringify(action, null, 2)} on:change={(event) => onStateEdit('action', index, (event.currentTarget as HTMLTextAreaElement).value)}></textarea>
              <button class="action-remove" aria-label="Remove action" title="Remove action" on:click={() => onStateEdit('action-remove', index, '')}>×</button>
            </div>
          {/each}
          <button class="state-add" on:click={() => onStateEdit('action-add', -1, '')}>+ Add action</button>
          <div class="eyebrow editor-label">ON EXIT ACTIONS</div>
          <p class="field-help">Run once when this state transitions to another state.</p>
          {#each selectedState.on_exit || [] as action, index}
            <div class="action-editor-row">
              <textarea class="code-editor" value={JSON.stringify(action, null, 2)} on:change={(event) => onStateEdit('on-exit', index, (event.currentTarget as HTMLTextAreaElement).value)}></textarea>
              <button class="action-remove" aria-label="Remove on-exit action" title="Remove on-exit action" on:click={() => onStateEdit('on-exit-remove', index, '')}>×</button>
            </div>
          {/each}
          <button class="state-add" on:click={() => onStateEdit('on-exit-add', -1, '')}>+ Add on-exit action</button>
          <div class="eyebrow editor-label">TRANSITIONS</div>
          {#each selectedState.transitions as transition, index}
            <div
              class="transition-row"
              class:dragging={draggedTransitionIndex === index}
              role="group"
              aria-label={`Transition ${index + 1}`}
              on:dragover={(event) => event.preventDefault()}
              on:drop={(event) => dropTransition(event, index)}
            >
              <button
                class="transition-drag"
                draggable={true}
                aria-label={`Move transition ${index + 1}`}
                title="Drag to reorder"
                on:dragstart={(event) => startTransitionDrag(event, index)}
                on:dragend={() => (draggedTransitionIndex = null)}
              >⋮⋮</button>
              <input aria-label="Transition condition" placeholder="condition or default" value={transition.default !== undefined ? 'default' : transition.condition || ''} on:change={(event) => onStateEdit('transition-condition', index, (event.currentTarget as HTMLInputElement).value)} />
              <input aria-label="Transition target" placeholder="target state" value={transition.then} on:change={(event) => onStateEdit('transition-target', index, (event.currentTarget as HTMLInputElement).value)} />
              <button class="transition-remove" aria-label="Remove transition" title="Remove transition" on:click={() => onStateEdit('transition-remove', index, '')}>×</button>
            </div>
          {/each}
          <button class="state-add" on:click={() => onStateEdit('transition-add', -1, '')}>+ Add transition</button>
        </div>
      {/if}
    {:else}
      <p class="muted">No backend procedure loaded. Fixture graph is active.</p>
    {/if}
  </div>
  <button class="primary" on:click={onValidate}>Validate draft</button>
  <button on:click={onSave}>Save procedure</button>
</aside>
