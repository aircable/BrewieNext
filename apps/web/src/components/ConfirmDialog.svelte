<script lang="ts">
  export let title = 'Confirm';
  export let message = '';
  export let confirmLabel = 'CONFIRM';
  export let danger = false;
  export let onConfirm: () => void;
  export let onCancel: () => void;

  function handleTouch(event: TouchEvent) {
    const touch = event.touches[0];
    const overlay = event.currentTarget as HTMLElement;
    if (!touch || !overlay || event.type !== 'touchstart') return;
    const rect = overlay.getBoundingClientRect();
    const x = Math.round(touch.clientY - rect.top);
    const y = 480 - Math.round(touch.clientX - rect.left);
    const controls = Array.from(overlay.querySelectorAll<HTMLElement>('[data-dialog-control]'));
    const hit = controls.find((control) => {
      let left = control.offsetLeft;
      let top = control.offsetTop;
      let parent = control.offsetParent as HTMLElement | null;
      while (parent && parent !== overlay) {
        left += parent.offsetLeft;
        top += parent.offsetTop;
        parent = parent.offsetParent as HTMLElement | null;
      }
      return x >= left && x < left + control.offsetWidth && y >= top && y < top + control.offsetHeight;
    });
    if (hit) { hit.click(); event.preventDefault(); }
  }
</script>

<div class="confirm-overlay" role="presentation" on:touchstart|capture={handleTouch}>
  <div class="confirm-dialog" role="alertdialog" aria-modal="true" aria-label={title}>
    <h2>{title}</h2>
    <p>{message}</p>
    <div>
      <button data-dialog-control="cancel" on:click={onCancel}>CANCEL</button>
      <button data-dialog-control="confirm" class:danger on:click={onConfirm}>{confirmLabel}</button>
    </div>
  </div>
</div>
