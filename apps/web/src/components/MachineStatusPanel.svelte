<script lang="ts">
  import type { MachineStatus } from '../lib/simulation';
  export let machine: MachineStatus;
  export let onBack: () => void = () => {};
  export let onCommand: (command: string) => void = () => {};

  type HeaterId = 'boil_heater' | 'mash_heater';
  let targetHeater: HeaterId | null = null;
  let targetDigits = '';
  let replaceTargetOnDigit = false;

  const fmt = (value: number) => Number.isFinite(value) ? value.toFixed(1) : '—';
  const heaterState = (device: HeaterId) => machine.heaters[device].state || (machine.heaters[device].active ? 'on' : machine.heaters[device].targetC !== null ? 'deferred' : 'off');
  function openHeaterTarget(device: HeaterId) {
    targetHeater = device;
    const target = machine.heaters[device].targetC;
    targetDigits = target === null ? '' : String(Math.round(target));
    replaceTargetOnDigit = target !== null;
  }
  function enterDigit(digit: number) {
    if (replaceTargetOnDigit) {
      targetDigits = String(digit);
      replaceTargetOnDigit = false;
    } else if (targetDigits.length < 3) targetDigits += String(digit);
  }
  function eraseDigit() {
    if (replaceTargetOnDigit) {
      targetDigits = '';
      replaceTargetOnDigit = false;
    } else if (targetDigits) targetDigits = targetDigits.slice(0, -1);
    else targetHeater = null;
  }
  function acceptTarget() {
    if (!targetHeater || !targetDigits) return;
    onCommand(`heater_target:${targetHeater}:${Number(targetDigits)}`);
    targetHeater = null;
  }
  $: boilTarget = machine.heaters.boil_heater.targetC;
  $: mashTarget = machine.heaters.mash_heater.targetC;
</script>

{#if targetHeater}
  <section class="brewmaster-surface heater-keypad" aria-label="Heater target entry">
    <div class="keypad-heading">{targetHeater === 'boil_heater' ? 'BOIL' : 'MASH'} HEATER TARGET °C</div>
    <output>{targetDigits || '–'}</output>
    <div class="keypad-grid">
      {#each [1, 2, 3, 4, 5, 6, 7, 8, 9] as digit}
        <button data-touch-control={`keypad-${digit}`} on:click={() => enterDigit(digit)}>{digit}</button>
      {/each}
      <button data-touch-control="keypad-erase" class="erase" on:click={eraseDigit}>ERASE</button>
      <button data-touch-control="keypad-0" on:click={() => enterDigit(0)}>0</button>
      <button data-touch-control="keypad-ok" class="ok" on:click={acceptTarget}>OK</button>
    </div>
    <small>Enter 0 to switch the heater off.</small>
  </section>
{:else}
<section class="brewmaster-surface" aria-label="Brewie Brewmaster machine status">
  <div class="control-row three">
    <button data-touch-control="toggle:boil_pump" class:active={machine.pumps.boil_pump} on:click={() => onCommand('toggle:boil_pump')}>BOIL PUMP</button>
    <button data-touch-control="toggle:boil_return_valve" class:active={machine.valves.boil_return_valve} on:click={() => onCommand('toggle:boil_return_valve')}>BOIL RETURN</button>
    <button data-touch-control="toggle:boil_inlet_valve" class:active={machine.valves.boil_inlet_valve} on:click={() => onCommand('toggle:boil_inlet_valve')}>BOIL INLET</button>
  </div>
  <div class="control-row three">
    <button data-touch-control="toggle:mash_pump" class:active={machine.pumps.mash_pump} on:click={() => onCommand('toggle:mash_pump')}>MASH PUMP</button>
    <button data-touch-control="toggle:mash_return_valve" class:active={machine.valves.mash_return_valve} on:click={() => onCommand('toggle:mash_return_valve')}>MASH RETURN</button>
    <button data-touch-control="toggle:mash_inlet_valve" class:active={machine.valves.mash_inlet_valve} on:click={() => onCommand('toggle:mash_inlet_valve')}>MASH INLET</button>
  </div>
  <div class="control-row four">
    {#each [1, 2, 3, 4] as cage}
      <button data-touch-control={`toggle:hop_cage_${cage}_valve`} class:active={machine.valves[`hop_cage_${cage}_valve`]} on:click={() => onCommand(`toggle:hop_cage_${cage}_valve`)}>HOP {cage}</button>
    {/each}
  </div>
  <div class="control-row two">
    <button data-touch-control="toggle:outlet_valve" class:active={machine.valves.outlet_valve} on:click={() => onCommand('toggle:outlet_valve')}>OUTLET VALVE</button>
    <button data-touch-control="toggle:wort_cooling_valve" class:active={machine.valves.wort_cooling_valve} on:click={() => onCommand('toggle:wort_cooling_valve')}>COOL VALVE</button>
  </div>
  <div class="control-row two">
    <button data-touch-control="toggle:water_inlet_valve" class:active={machine.valves.water_inlet_valve} on:click={() => onCommand('toggle:water_inlet_valve')}>WATER INLET</button>
    <button data-touch-control="toggle:cooling_water_inlet_valve" class:active={machine.valves.cooling_water_inlet_valve} on:click={() => onCommand('toggle:cooling_water_inlet_valve')}>COOL INLET</button>
  </div>
  <div class="control-row two">
    <button data-touch-control="target:boil_heater" class:heater-on={heaterState('boil_heater') === 'on'} class:heater-deferred={heaterState('boil_heater') === 'deferred'} on:click={() => openHeaterTarget('boil_heater')}>BOIL HEATER</button>
    <button data-touch-control="target:mash_heater" class:heater-on={heaterState('mash_heater') === 'on'} class:heater-deferred={heaterState('mash_heater') === 'deferred'} on:click={() => openHeaterTarget('mash_heater')}>MASH HEATER</button>
  </div>
  <div class="control-row one"><button data-touch-control="close_all" class="close-all" on:click={() => onCommand('close_all')}>CLOSE ALL</button></div>
  <div class="control-row navigation">
    <button data-touch-control="reset_level" class="reset-level" on:click={() => onCommand('reset_level')}><span>RESET<br>LEVEL</span></button>
    <button data-touch-control="brewmaster-back" class="back" on:click={onBack}>BACK TO MAIN SCREEN</button>
  </div>

  <div class="tank-status" aria-label="Boil and mash tank readings">
    <strong>{fmt(machine.sensors.boilVolumeL)}L</strong><span>LEVEL</span><strong>{fmt(machine.sensors.mashVolumeL)}L</strong>
    <strong>{fmt(machine.sensors.tempBoilC)}C</strong><span>TEMP</span><strong>{fmt(machine.sensors.tempMashC)}C</strong>
    <strong>{boilTarget === null ? '–' : `${fmt(boilTarget)}C`}</strong><span>TARGET</span><strong>{mashTarget === null ? '–' : `${fmt(mashTarget)}C`}</strong>
    <strong>{machine.pumps.boil_pump ? 'ON' : 'OFF'}</strong><span>PUMP</span><strong>{machine.pumps.mash_pump ? 'ON' : 'OFF'}</strong>
  </div>
</section>
{/if}
