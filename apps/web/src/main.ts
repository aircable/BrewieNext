import './styles.css';
import { mount } from 'svelte';
import App from './App.svelte';

const debugPanel = document.getElementById('boot-debug');
function debug(message: string) {
  if (debugPanel && window.location.search.indexOf('debug=1') !== -1) {
    debugPanel.textContent += `\n${message}`;
  }
}

debug(`JS loaded; viewport ${window.innerWidth}x${window.innerHeight}`);

let app: ReturnType<typeof mount>;
try {
  app = mount(App, { target: document.getElementById('app')! });
  debug('Svelte mounted');
} catch (error) {
  debug(`MOUNT ERROR: ${error instanceof Error ? error.message : String(error)}`);
  throw error;
}

export default app;
