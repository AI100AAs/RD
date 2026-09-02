# roomform

roomform is an AI-assisted interior design studio. Upload a photo of a room,
describe the atmosphere or changes you want, and explore two visual redesign
directions before moving anything in the real space. You can request another
pair whenever you want more possibilities.

This project was developed as a sample for UBC's **AI 100: Introduction to
Artificial Intelligence** course.

## How It Works

1. Upload a PNG, JPG, or WEBP photo of a room.
2. Choose optional presets for the room type, design style, and feeling, then
   add any extra requirements in your own words.
3. The course language model examines the photo and turns the rough idea into a
   concise, image-ready direction.
4. Review and edit that direction before approving it.
5. The course image service creates two sequential variations using the
   original room photo and the approved direction.
6. Request another pair of sequential variations if you want to keep exploring.
7. Download individual results or revisit them from **Creation history**.

The interface also includes light/dark mode, responsive layouts for mobile and
desktop, progress feedback for long-running generation, and partial-result
handling when one image variation fails.

## Tech Stack

- Flask backend serving the application and JSON/SSE API
- SQLite persistence for generated room creations and history
- Build-free HTML, CSS, and JavaScript frontend
- Course LLM service for photo-aware prompt refinement
- Course media service using `instruct-pix2pix` for room image editing
- Server-sent events (SSE) for streaming generated variations to the browser

## Repository Layout

- `server/gizmoapp_server/templates/index.html` contains the roomform studio.
- `server/gizmoapp_server/templates/history.html` contains the creation archive.
- `server/gizmoapp_server/static/app/main.js` handles upload, refinement,
  generation, progress, and result rendering.
- `server/gizmoapp_server/api.py` defines the room refinement, generation, and
  history endpoints.
- `server/gizmoapp_server/llm.py` and `media.py` provide fail-closed helpers for
  the course AI services.
- `server/gizmoapp_server/db.py` manages the SQLite database and migrations.
- `tests/` contains backend and routing tests.
- `deploy/app-shell.txt` selects the graphical public shell for the hosted app.
- `docs/course-media.md` documents the available image and speech operations.

## Local Development

The project uses Python and intentionally has no frontend build step or Node
dependency.

1. Optionally create local settings:

   ```bash
   cp .env.example .env
   ```

2. Install the Python dependencies:

   ```bash
   ALLOW_NETWORK_INSTALL=1 make install
   ```

3. Initialize the SQLite database:

   ```bash
   make init-db
   ```

4. Start the development server:

   ```bash
   ALLOW_SERVER_RUN=1 make dev-graphical
   ```

The default URL is `http://127.0.0.1:8001/`. To test deployment under a path
prefix, set `GIZMOAPP_URL_PREFIX=/roomform` in `.env`; routes and assets will
then be served under `/roomform/`.

## AI Services

The hosted AI100/CodingWorkspace environment injects credentials at runtime.
The app expects:

- `GIZMO_LLM_API_KEY`, `GIZMO_LLM_BASE_URL`, and `GIZMO_LLM_MODEL` for prompt
  refinement
- `GIZMO_MEDIA_BASE_URL`, `GIZMO_MEDIA_API_KEY`, and
  `GIZMO_MEDIA_OPERATIONS` for image editing

These values must not be committed, exposed to browser JavaScript, returned in
API responses, or written to logs. The browser calls the app's Flask routes;
only the server-side helpers contact the course services.

Image generation is deliberately sequential because the GPU worker is shared.
Each request creates two variations, and the interface can request another pair
without losing the results already shown. Each variation has one retry when the
worker reports that it is busy. Errors are streamed to the UI so a user can
distinguish a temporary worker failure, invalid credentials, or a partial result.
A hosted preview may need to be restarted when its media credential has expired
or is unavailable.

## API Endpoints

All endpoints respect `GIZMOAPP_URL_PREFIX` when it is configured.

- `POST /api/room/refine` accepts a room image and brief, then returns a refined
  image-generation prompt.
- `POST /api/room/generate` accepts an image, approved prompt, and optional
  `start_option`, then streams two sequential options through `progress`,
  `image`, `variation-error`, `done`, or `error` SSE events.
- `GET /api/room/history` returns saved generated creations.
- `GET /healthz` reports process liveness.
- `GET /readyz` checks SQLite readiness.
- `GET /api/bootstrap` returns app metadata and runtime information.

The room endpoints validate image type, file size, prompt length, and request
shape before invoking an external service.

## Validation

Run the standard checks before making a change:

```bash
make validate
```

For the JavaScript structural check only:

```bash
make js-check
```

The graphical shell can optionally be checked with the repository's browser
visual pipeline when Playwright and Chromium are already available:

```bash
ALLOW_BROWSER_CHECK=1 make visual-check
```

## Deployment Notes

The app is designed to run behind Gunicorn and an nginx path-prefix route. The
tracked `deploy/app-shell.txt` currently contains `graphical`, which selects
the roomform studio for hosted previews. Machine-specific secrets, ports, and
database paths belong in `.env`; do not commit them.

Deployment scripts and nginx examples are kept under `deploy/` and `scripts/`.
They are administrative operations and should only be run explicitly on the
target server, not as part of ordinary local development or validation.
