<script lang="ts">
  import type { BrewSession } from '../lib/model';
  import type { MachineStatus } from '../lib/simulation';
  import MachineStatusPanel from './MachineStatusPanel.svelte';
  export let session: BrewSession;
  export let machine: MachineStatus;
  export let onControl: (control: string) => void = () => {};
  export let initialView: 'procedure' | 'machine' | undefined = undefined;
  let activeControl = '';
  let screenView: 'procedure' | 'machine' = initialView || (typeof window !== 'undefined' && window.location.search.indexOf('machine=1') !== -1 ? 'machine' : 'procedure');
  let touchMarker = { visible: false, x: 0, y: 0 };
  $: recovering = session.screen.allowed_controls?.includes('recover');
  $: failure = session.screen.failure;
  $: terminal = ['complete', 'error', 'aborted'].includes(session.status);
  $: primaryControl = session.status === 'idle' ? 'start' : terminal ? 'restart' : 'pause';
  $: primaryLabel = session.status === 'paused' ? 'RESUME' : primaryControl.toUpperCase();

  function activate(control: string) {
    activeControl = control;
    window.setTimeout(() => {
      if (activeControl === control) activeControl = '';
    }, 180);
  }

  function control(control: string) {
    activate(control);
    onControl(control);
  }

  function readable(value: string) {
    return value.replace(/_/g, ' ');
  }

  function offsetWithinScreen(element: HTMLElement, screen: HTMLElement) {
    let left = element.offsetLeft;
    let top = element.offsetTop;
    let parent = element.offsetParent as HTMLElement | null;
    while (parent && parent !== screen) {
      left += parent.offsetLeft;
      top += parent.offsetTop;
      parent = parent.offsetParent as HTMLElement | null;
    }
    return { left, top };
  }

  function handleTouch(event: TouchEvent) {
    const touch = event.touches[0];
    const screen = event.currentTarget as HTMLElement;
    if (!touch || !screen) return;
    const rect = screen.getBoundingClientRect();
    // The portrait UI is rotated 90 degrees clockwise into the landscape
    // framebuffer. Undo that CSS transform before drawing or hit-testing.
    const portraitX = Math.round(touch.clientY - rect.top);
    const portraitY = 480 - Math.round(touch.clientX - rect.left);
    touchMarker = { visible: true, x: portraitX, y: portraitY };
    if (event.type !== 'touchstart') return;
    const selector = session.status === 'error'
      ? '.runtime-error-overlay [data-touch-control]'
      : '[data-touch-control]';
    const controls = Array.from(screen.querySelectorAll<HTMLElement>(selector));
    const hit = controls.find((control) => {
      const position = offsetWithinScreen(control, screen);
      return portraitX >= position.left && portraitX < position.left + control.offsetWidth
        && portraitY >= position.top && portraitY < position.top + control.offsetHeight;
    });
    if (hit) {
      activate(hit.dataset.touchControl || '');
      hit.click();
      event.preventDefault();
    }
  }
</script>

<section class="brewie-screen" aria-label="Active Brewie screen" on:touchstart|capture={handleTouch} on:touchmove|capture={handleTouch}>
  {#if touchMarker.visible}
    <div class="touch-marker" style={`left: ${touchMarker.x - 10}px; top: ${touchMarker.y - 10}px;`} aria-hidden="true"></div>
  {/if}
  <header><strong>BREWIE NEXT</strong><span class:paused={session.status === 'paused'} class:error={session.status === 'error'}>{session.status === 'error' ? 'ATTENTION' : session.status.toUpperCase()}</span></header>
  <div class:machine-view={screenView === 'machine'} class:procedure-view={screenView === 'procedure'} class="screen-body">
    {#if screenView === 'procedure'}
      <h2>{session.screen.title}</h2>
      <p>{session.screen.message}</p>
      <div class="procedure-progress">
        {#if session.screen.progress !== null}
          <div class="progress"><span style={`width: ${session.screen.progress}%`}></span></div>
        {/if}
      </div>
      <div class="screen-readouts" class:empty={!session.screen.readouts?.length} aria-label="Procedure readouts">
        {#each session.screen.readouts || [] as readout}
          <div class="screen-readout">
            <span>{readout.label}</span>
            <strong>{readout.value}{readout.unit ? ` ${readout.unit}` : ''}</strong>
          </div>
        {/each}
      </div>
      <p class="screen-footer-message">{session.screen.footer_message || ''}</p>
      {#if session.screen.choices.length}
        <div class="choices">
          {#each session.screen.choices as choice}
            <button data-touch-control={`input:${choice.value}`} class:pressed={activeControl === `input:${choice.value}`} on:click={() => control(`input:${choice.value}`)}>{choice.label}</button>
          {/each}
        </div>
      {/if}
      <div class="procedure-button-block">
        {#if session.status === 'idle' || terminal}
          <div class="procedure-navigation single">
            <button data-touch-control="programs" class:pressed={activeControl === 'programs'} on:click={() => control('programs')}>← PROGRAMS</button>
          </div>
        {:else}
          <div class="procedure-navigation">
            <button data-touch-control="previous_procedure" class:pressed={activeControl === 'previous_procedure'} on:click={() => control('previous_procedure')}>PREVIOUS<br>PROCEDURE</button>
            <button data-touch-control="next_procedure" class:pressed={activeControl === 'next_procedure'} on:click={() => control('next_procedure')}>NEXT<br>PROCEDURE</button>
          </div>
        {/if}
        {#if terminal}
          <div class="screen-controls single">
            <button data-touch-control="restart" class:pressed={activeControl === 'restart'} on:click={() => control('restart')}>RESTART THIS PROGRAM</button>
          </div>
        {:else if recovering}
          <div class="screen-controls">
            <button data-touch-control="recover" on:click={() => control('recover')}>RESTART<br>PROCEDURE</button>
            <button data-touch-control="discard" class="danger" on:click={() => control('discard')}>DISCARD</button>
          </div>
        {:else}
          <div class="screen-controls" class:single={session.status === 'idle'}>
            <button data-touch-control={primaryControl} class:pressed={activeControl === primaryControl} on:click={() => control(primaryControl)}>{primaryLabel}</button>
            {#if session.status !== 'idle'}
              <button data-touch-control="abort" class:pressed={activeControl === 'abort'} class="danger" on:click={() => control('abort')}>ABORT</button>
            {/if}
          </div>
        {/if}
      </div>
    {:else}
      <MachineStatusPanel {machine} onCommand={onControl} onBack={() => screenView = 'procedure'} />
    {/if}
  </div>
  {#if screenView === 'procedure'}
    <button class="screen-view-toggle" data-touch-control="screen-view" class:pressed={activeControl === 'screen-view'} on:click={() => { activate('screen-view'); screenView = 'machine'; }}>
      MACHINE STATUS
    </button>
  {/if}
  {#if session.status === 'error' && failure}
    <div class="runtime-error-overlay" role="alertdialog" aria-modal="true" aria-label="Brewing needs attention">
      <div class="runtime-error-card">
        <strong>{failure.kind === 'timeout' ? 'STEP TIMED OUT' : 'PROCEDURE ERROR'}</strong>
        <h2>{failure.state_description || readable(failure.state || 'Unknown state')}</h2>
        {#if failure.timeout_s}
          <p>No valid transition occurred within {Math.round(failure.timeout_s)} seconds.</p>
        {:else}
          <p>{failure.message}</p>
        {/if}
        {#each (failure.criteria || []).slice(0, 2) as criterion}
          <div class="error-criterion">
            <span>Required</span><code>{readable(criterion.expression)}</code>
            {#if criterion.observed?.length}
              <small>Observed: {criterion.observed.map((item) => `${readable(item.name)} = ${item.value}`).join(', ')}</small>
            {/if}
          </div>
        {/each}
        {#if failure.safe_shutdown_confirmed === false}
          <p class="shutdown-warning">Outputs could not be confirmed safe. Disconnect heater power.</p>
        {/if}
        <div class="error-actions" class:single={!session.screen.allowed_controls.includes('retry')}>
          {#if session.screen.allowed_controls.includes('retry')}
            <button data-touch-control="retry" class:pressed={activeControl === 'retry'} on:click={() => control('retry')}>RETRY</button>
            <button data-touch-control="skip" class:pressed={activeControl === 'skip'} on:click={() => control('skip')}>SKIP</button>
          {/if}
          <button data-touch-control="abort" class="danger" class:pressed={activeControl === 'abort'} on:click={() => control('abort')}>ABORT</button>
        </div>
      </div>
    </div>
  {/if}
</section>
