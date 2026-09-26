# Issue tracker: Local Markdown

Specs and issues live as Markdown files under `.scratch/`.

## Conventions

- One feature per directory: `.scratch/<feature-slug>/`
- Feature spec: `.scratch/<feature-slug>/spec.md`
- Implementation tickets: `.scratch/<feature-slug>/issues/<NN>-<slug>.md`
- Each ticket records its state with a `Status:` line
- A completed spec uses `Status: ready-for-agent`
- Comments and history are appended under `## Comments`

## Publishing and retrieval

When a skill says "publish," create the appropriate file under `.scratch/<feature-slug>/`.
When a skill says "fetch," read the referenced Markdown file directly.

## Wayfinding

- Map: `.scratch/<effort>/map.md`
- Child ticket: `.scratch/<effort>/issues/<NN>-<slug>.md`
- Dependencies: `Blocked by: NN, NN`
- Claim work by setting `Status: claimed`
- Resolve work by adding `## Answer`, setting `Status: resolved`, and updating the map
