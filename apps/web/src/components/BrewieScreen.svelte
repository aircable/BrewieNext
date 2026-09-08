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
  $: primaryControl = session.status === 'idle' ? 'start' : session.status === 'complete' || session.status === 'error' ? 'reset' : 'pause';
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
    const portraitX = 272 - Math.round(touch.clientY - rect.top);
    const portraitY = Math.round(touch.clientX - rect.left);
    touchMarker = { visible: true, x: portraitX, y: portraitY };
    if (event.type !== 'touchstart') return;
    const controls = Array.from(screen.querySelectorAll<HTMLElement>('[data-touch-control]'));
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
  <header><strong>BREWIE NEXT</strong><span class:paused={session.status === 'paused'}>{session.status.toUpperCase()}</span></header>
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
        {#if session.status === 'idle'}
          <div class="procedure-navigation single">
            <button data-touch-control="programs" class:pressed={activeControl === 'programs'} on:click={() => control('programs')}>← PROGRAMS</button>
          </div>
        {:else}
          <div class="procedure-navigation">
            <button data-touch-control="previous_procedure" class:pressed={activeControl === 'previous_procedure'} on:click={() => control('previous_procedure')}>PREVIOUS<br>PROCEDURE</button>
            <button data-touch-control="next_procedure" class:pressed={activeControl === 'next_procedure'} on:click={() => control('next_procedure')}>NEXT<br>PROCEDURE</button>
          </div>
        {/if}
        <div class="screen-controls">
          <button data-touch-control={primaryControl} class:pressed={activeControl === primaryControl} on:click={() => control(primaryControl)}>{primaryLabel}</button>
          <button data-touch-control="abort" class:pressed={activeControl === 'abort'} class="danger" on:click={() => control('abort')}>ABORT</button>
        </div>
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
</section>
