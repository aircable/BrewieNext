<script lang="ts">
  import type { ProgramSummary } from '../lib/model';

  export let programs: ProgramSummary[] = [];
  export let onSelect: (program: ProgramSummary) => void;

  let activeProgram = '';
  let touchMarker = { visible: false, x: 0, y: 0 };
  const categoryLabels: Record<ProgramSummary['category'], string> = {
    brewing: 'BREWING',
    cleaning: 'CLEANING',
    maintenance: 'MAINTENANCE',
    diagnostics: 'DIAGNOSTICS'
  };
  $: categories = [...new Set(programs.map((program) => program.category))];

  function select(program: ProgramSummary) {
    if (program.status !== 'available') return;
    activeProgram = program.id;
    window.setTimeout(() => {
      if (activeProgram === program.id) activeProgram = '';
    }, 180);
    onSelect(program);
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
    const controls = Array.from(screen.querySelectorAll<HTMLElement>('[data-touch-control]:not(:disabled)'));
    const hit = controls.find((control) => {
      const position = offsetWithinScreen(control, screen);
      return portraitX >= position.left && portraitX < position.left + control.offsetWidth
        && portraitY >= position.top && portraitY < position.top + control.offsetHeight;
    });
    if (hit) {
      hit.click();
      event.preventDefault();
    }
  }
</script>

<section class="brewie-screen program-screen" aria-label="Brewie program selection" on:touchstart|capture={handleTouch} on:touchmove|capture={handleTouch}>
  {#if touchMarker.visible}
    <div class="touch-marker" style={`left: ${touchMarker.x - 10}px; top: ${touchMarker.y - 10}px;`} aria-hidden="true"></div>
  {/if}
  <header><strong>BREWIE NEXT</strong><span>READY</span></header>
  <div class="screen-body local-program-view">
    <div>
      <h2>Programs</h2>
      <p>What would you like the Brewie to do?</p>
    </div>
    <div class="local-program-list">
      {#each categories as category}
        <div class="local-program-category">
          <small>{categoryLabels[category]}</small>
          {#each programs.filter((program) => program.category === category) as program}
            <button
              data-touch-control={`program:${program.id}`}
              class:pressed={activeProgram === program.id}
              class:planned={program.status !== 'available'}
              disabled={program.status !== 'available'}
              on:click={() => select(program)}
            >
              <strong>{program.label}</strong>
              <span>{program.status === 'available' ? 'OPEN' : 'IN DESIGN'}</span>
            </button>
          {/each}
        </div>
      {/each}
    </div>
  </div>
  <div class="program-screen-footer">SELECT A PROGRAM</div>
</section>
