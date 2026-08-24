# Development Services

## Complete local simulator

From the repository root:

```sh
npm install
npm run dev
```

This command prepares `.dev/`, starts the Flask backend and authoritative
workflow runner at `127.0.0.1:8081`, waits for its health check, and then starts
Vite at `127.0.0.1:5173`. The frontend proxies `/api` requests to the backend.
Do not run the frontend workspace by itself unless a backend is already active.

Development always uses `BREWIE_AVR_ENABLED=0` and the editable procedure copy
under `.dev/programs/workspace`.

## Separate processes

For API debugging, run:

```sh
npm run backend
```

In another terminal, run:

```sh
npm run dev:web
```

Useful checks:

```sh
curl http://127.0.0.1:8081/api/health
curl http://127.0.0.1:8081/api/procedures
```

Set `EDITOR_PORT` before `npm run backend` to select another backend port. If
you do so, also update the Vite proxy in `apps/web/vite.config.ts`.

## ReLinux appliance

The installed application uses these services:

```sh
/etc/init.d/S85brewie-backend start
/etc/init.d/S85brewie-backend restart
/etc/init.d/S85brewie-backend stop
```

The backend reads the active application from `/usr/share/brewie/current`, the
active program release from `/var/lib/brewie/programs/current`, and persistent
recipes from `/var/lib/brewie/recipes`. It listens on port 8081. Confirm it on
the appliance with:

```sh
wget -qO- http://127.0.0.1:8081/api/health
```

Appliance mode defaults AVR access to `auto`. Restarting the backend performs
the configured safe start and can interrupt a running brew, so production
service restarts must be deliberate.
