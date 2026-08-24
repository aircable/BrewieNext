<script lang="ts">
  import type { Fermentable, HopAddition, MashStep, Recipe, RecipeSummary } from '../lib/model';

  export let recipes: RecipeSummary[] = [];
  export let recipe: Recipe | null = null;
  export let onSelect: (id: string) => void;
  export let onChange: (recipe: Recipe) => void;
  export let onValidate: () => void;
  export let onSave: () => void;
  export let onUse: () => void;
  export let onNew: () => void;

  const numberValue = (event: Event) => Number((event.currentTarget as HTMLInputElement).value);
  const textValue = (event: Event) => (event.currentTarget as HTMLInputElement).value;

  $: activeHops = recipe
    ? recipe.boil.hop_additions
        .map((addition) => ({
          ...addition,
          releaseAfter: recipe!.boil.duration_min - addition.minutes_remaining
        }))
        .sort((left, right) => right.minutes_remaining - left.minutes_remaining)
    : [];
  $: initialUnhopped = recipe && activeHops.length
    ? recipe.boil.duration_min - Math.max(...activeHops.map((addition) => addition.minutes_remaining))
    : recipe?.boil.duration_min || 0;

  function changeRoot(field: 'id' | 'name' | 'style' | 'description' | 'batch_volume_L', value: string | number) {
    if (!recipe) return;
    onChange({ ...recipe, [field]: value });
  }

  function changeWater(field: 'mash_volume_L' | 'sparge_volume_L', value: number) {
    if (!recipe) return;
    onChange({ ...recipe, water: { ...recipe.water, [field]: value } });
  }

  function changeMashIn(value: number) {
    if (!recipe) return;
    onChange({ ...recipe, mash: { ...recipe.mash, mash_in_temperature_C: value } });
  }

  function changeFermentable(index: number, field: keyof Fermentable, value: string | number) {
    if (!recipe) return;
    const fermentables = recipe.fermentables.map((item, itemIndex) => itemIndex === index ? { ...item, [field]: value } : item);
    onChange({ ...recipe, fermentables });
  }

  function addFermentable() {
    if (!recipe) return;
    onChange({ ...recipe, fermentables: [...recipe.fermentables, { name: 'New grain', amount_kg: 1, bag: 1 }] });
  }

  function removeFermentable(index: number) {
    if (!recipe) return;
    onChange({ ...recipe, fermentables: recipe.fermentables.filter((_, itemIndex) => itemIndex !== index) });
  }

  function changeMashStep(index: number, field: keyof MashStep, value: string | number) {
    if (!recipe) return;
    const steps = recipe.mash.steps.map((step, stepIndex) => stepIndex === index ? { ...step, [field]: value } : step);
    onChange({ ...recipe, mash: { ...recipe.mash, steps } });
  }

  function changeBoil(field: 'duration_min' | 'target_temperature_C', value: number) {
    if (!recipe) return;
    onChange({ ...recipe, boil: { ...recipe.boil, [field]: value } });
  }

  function changeHop(index: number, field: keyof HopAddition, value: string | number) {
    if (!recipe) return;
    const hop_additions = recipe.boil.hop_additions.map((addition, additionIndex) => additionIndex === index
      ? { ...addition, [field]: value }
      : addition);
    onChange({ ...recipe, boil: { ...recipe.boil, hop_additions } });
  }

  function changeSparge(field: 'target_temperature_C' | 'cycle_count', value: number) {
    if (!recipe) return;
    const sparge = recipe.sparge || { target_temperature_C: 78, cycle_count: 5 };
    onChange({ ...recipe, sparge: { ...sparge, [field]: value } });
  }

  function changeCooling(value: number) {
    if (!recipe) return;
    onChange({ ...recipe, cooling: { ...recipe.cooling, target_temperature_C: value } });
  }

  function changeSedimentation(value: number) {
    if (!recipe) return;
    onChange({ ...recipe, sedimentation: { duration_min: value } });
  }
</script>

<section class="recipe-studio">
  <header class="recipe-toolbar">
    <div>
      <div class="eyebrow">RECIPE LIBRARY</div>
      <select aria-label="Selected recipe" value={recipe?.id || ''} on:change={(event) => onSelect((event.currentTarget as HTMLSelectElement).value)}>
        {#if recipe && !recipes.some((item) => item.id === recipe?.id)}
          <option value={recipe.id}>{recipe.name} · unsaved</option>
        {/if}
        {#each recipes as item}
          <option value={item.id}>{item.name} · {item.source}</option>
        {/each}
      </select>
    </div>
    <div class="recipe-actions">
      <button on:click={onNew}>+ New recipe</button>
      <button on:click={onValidate}>Validate</button>
      <button on:click={onSave}>Save recipe</button>
      <button class="primary" on:click={onUse}>Use for brew</button>
    </div>
  </header>

  {#if recipe}
    <div class="recipe-form">
      <section class="recipe-card recipe-overview">
        <div class="eyebrow">OVERVIEW</div>
        <label>Recipe ID<input value={recipe.id} on:change={(event) => changeRoot('id', textValue(event).trim())} /></label>
        <label>Name<input value={recipe.name} on:input={(event) => changeRoot('name', textValue(event))} /></label>
        <label>Style<input value={recipe.style || ''} on:input={(event) => changeRoot('style', textValue(event))} /></label>
        <label>Batch volume (L)<input type="number" min="0" step="0.1" value={recipe.batch_volume_L} on:input={(event) => changeRoot('batch_volume_L', numberValue(event))} /></label>
        <label>Description<textarea value={recipe.description || ''} on:input={(event) => changeRoot('description', (event.currentTarget as HTMLTextAreaElement).value)}></textarea></label>
      </section>

      <section class="recipe-card">
        <div class="eyebrow">WATER</div>
        <div class="recipe-grid two">
          <label>Mash water (L)<input type="number" min="0" step="0.1" value={recipe.water.mash_volume_L} on:input={(event) => changeWater('mash_volume_L', numberValue(event))} /></label>
          <label>Sparge water (L)<input type="number" min="0" step="0.1" value={recipe.water.sparge_volume_L} on:input={(event) => changeWater('sparge_volume_L', numberValue(event))} /></label>
        </div>
      </section>

      <section class="recipe-card wide">
        <div class="recipe-card-heading"><div class="eyebrow">FERMENTABLES / GRAIN LOADING</div><button on:click={addFermentable}>+ Grain</button></div>
        {#each recipe.fermentables as fermentable, index}
          <div class="recipe-row grain-row">
            <input aria-label="Grain name" value={fermentable.name} on:input={(event) => changeFermentable(index, 'name', textValue(event))} />
            <input aria-label="Grain kilograms" type="number" min="0" step="0.01" value={fermentable.amount_kg} on:input={(event) => changeFermentable(index, 'amount_kg', numberValue(event))} />
            <input aria-label="Grain bag" value={fermentable.bag || ''} on:input={(event) => changeFermentable(index, 'bag', textValue(event))} />
            <button aria-label="Remove grain" on:click={() => removeFermentable(index)}>×</button>
          </div>
        {/each}
      </section>

      <section class="recipe-card wide">
        <div class="recipe-card-heading"><div><div class="eyebrow">TWO-REST MASH SCHEDULE</div><p class="field-help">Modern malts use two editable temperature rests.</p></div></div>
        <label class="compact-field">Mash-in temperature (°C)<input type="number" step="0.1" value={recipe.mash.mash_in_temperature_C} on:input={(event) => changeMashIn(numberValue(event))} /></label>
        {#each recipe.mash.steps as step, index}
          <div class="recipe-row mash-row">
            <span class="row-number">{index + 1}</span>
            <input aria-label="Mash step name" value={step.name} on:input={(event) => changeMashStep(index, 'name', textValue(event))} />
            <input aria-label="Mash step temperature" type="number" step="0.1" value={step.target_temperature_C} on:input={(event) => changeMashStep(index, 'target_temperature_C', numberValue(event))} />
            <input aria-label="Mash step duration" type="number" min="0" step="1" value={step.duration_min} on:input={(event) => changeMashStep(index, 'duration_min', numberValue(event))} />
            <span></span>
          </div>
        {/each}
        <div class="recipe-row-labels mash-labels"><span></span><span>Step</span><span>°C</span><span>Minutes</span><span></span></div>
      </section>

      <section class="recipe-card wide">
        <div class="eyebrow">BOIL & HOP CAGES</div>
        <div class="recipe-grid two">
          <label>Boil duration (min)<input type="number" min="1" step="1" value={recipe.boil.duration_min} on:input={(event) => changeBoil('duration_min', numberValue(event))} /></label>
          <label>Boil target (°C)<input type="number" step="0.1" value={recipe.boil.target_temperature_C} on:input={(event) => changeBoil('target_temperature_C', numberValue(event))} /></label>
        </div>

        <div class="hop-timeline" aria-label="Hop release timeline">
          <div class="timeline-track">
            {#each activeHops as hop}
              <span class="timeline-marker" style={`left:${recipe.boil.duration_min ? (hop.releaseAfter / recipe.boil.duration_min) * 100 : 0}%`} title={`Cage ${hop.cage}: ${hop.hop}`}>
                <b>{hop.cage}</b><small>{hop.minutes_remaining}m left</small>
              </span>
            {/each}
          </div>
          <div class="timeline-axis"><span>Start</span><span>{initialUnhopped} min initially unhopped</span><span>End · 0 remaining</span></div>
        </div>

        {#each recipe.boil.hop_additions as hop, index}
          <div class="recipe-row hop-row">
            <strong>Cage {hop.cage}</strong>
            <input aria-label={`Hop name for cage ${hop.cage}`} placeholder="Empty or hop variety" value={hop.hop || ''} on:input={(event) => changeHop(index, 'hop', textValue(event))} />
            <input aria-label={`Hop grams for cage ${hop.cage}`} type="number" min="0" step="0.1" placeholder="g" value={hop.amount_g || 0} on:input={(event) => changeHop(index, 'amount_g', numberValue(event))} />
            <input aria-label={`Minutes remaining for cage ${hop.cage}`} type="number" min="0" step="1" placeholder="min left" value={hop.minutes_remaining} on:input={(event) => changeHop(index, 'minutes_remaining', numberValue(event))} />
            <span class="release-time">Always releases after {recipe.boil.duration_min - hop.minutes_remaining} min</span>
          </div>
        {/each}
      </section>

      <section class="recipe-card">
        <div class="eyebrow">SPARGE</div>
        <label>Target temperature (°C)<input type="number" step="0.1" value={recipe.sparge?.target_temperature_C || 78} on:input={(event) => changeSparge('target_temperature_C', numberValue(event))} /></label>
        <label>Cycles<input type="number" min="1" max="10" step="1" value={recipe.sparge?.cycle_count || 5} on:input={(event) => changeSparge('cycle_count', numberValue(event))} /></label>
      </section>

      <section class="recipe-card">
        <div class="eyebrow">COOLING</div>
        <label>Target temperature (°C)<input type="number" step="0.1" value={recipe.cooling.target_temperature_C} on:input={(event) => changeCooling(numberValue(event))} /></label>
      </section>

      <section class="recipe-card">
        <div class="eyebrow">SEDIMENTATION</div>
        <label>Undisturbed rest (min)<input type="number" min="1" step="1" value={recipe.sedimentation.duration_min} on:input={(event) => changeSedimentation(numberValue(event))} /></label>
      </section>
    </div>
  {:else}
    <p class="muted">No recipe loaded. Start the backend and select a recipe.</p>
  {/if}
</section>
