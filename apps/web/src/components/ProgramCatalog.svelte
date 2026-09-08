<script lang="ts">
  import type { ProgramSummary } from '../lib/model';

  export let programs: ProgramSummary[] = [];
  export let onOpen: (program: ProgramSummary) => void;

  const categoryLabels: Record<ProgramSummary['category'], string> = {
    brewing: 'Brewing',
    cleaning: 'Cleaning',
    maintenance: 'Maintenance',
    diagnostics: 'Diagnostics'
  };

  $: categories = [...new Set(programs.map((program) => program.category))];
</script>

<section class="program-catalog">
  <div class="program-catalog-heading">
    <div>
      <div class="eyebrow">MACHINE PROGRAMS</div>
      <h1>What would you like the Brewie to do?</h1>
      <p>Select a program to inspect its workflow and procedures.</p>
    </div>
  </div>

  {#each categories as category}
    <section class="program-category">
      <h2>{categoryLabels[category]}</h2>
      <div class="program-grid">
        {#each programs.filter((program) => program.category === category) as program}
          <button
            class:program-available={program.status === 'available'}
            class="program-card"
            on:click={() => onOpen(program)}
            aria-label={`${program.label}, ${program.status === 'available' ? 'available' : 'in design'}`}
          >
            <span class:available={program.status === 'available'} class="program-status">
              {program.status === 'available' ? 'AVAILABLE' : 'IN DESIGN'}
            </span>
            <strong>{program.label}</strong>
            <p>{program.description}</p>
            <small>{program.status === 'available' ? 'Open workflow →' : 'Open draft workflow →'}</small>
          </button>
        {/each}
      </div>
    </section>
  {/each}
</section>
