<script lang="ts">
  export let message = 'WORKING…';
  export let detail = 'Please wait.';
</script>

<div class="busy-overlay" role="status" aria-live="assertive" aria-label={message}>
  <div class="busy-card">
    <span class="busy-spinner" aria-hidden="true"></span>
    <strong>{message}</strong>
    <small>{detail}</small>
  </div>
</div>
