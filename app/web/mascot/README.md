# Glim: mascot art

`glim-template.svg` is the drawing template. When the art is ready, save it as `glim.svg` with the same layer ids.

## Layers (ids must match exactly)

| Layer | What to draw |
|---|---|
| `shadow` | Soft ground ellipse; a gradient to transparent |
| `body` | Droplet fill, translucent teal `#7FB8A4`, ~70% opacity |
| `core` | Amber core `#FFBE5A` plus its glow (radial gradient) |
| `core-icon` | A placeholder square where the app swaps in icons (globe, pen…) |
| `highlight` | Glass shine, upper left |
| `outline` | Droplet outline `#2F5D50`, ~6–8 px stroke, no fill |
| `eyes-open`, `eyes-closed` | One eye style per layer |
| `mouth-neutral`, `mouth-smile`, `mouth-o` | One mouth per layer |
| `cheeks` | Optional blush |
| `paws` | Nubbins covering the eyes (paused state) |

## Rules
- 256×256 canvas. Keep the droplet inside the dashed safe area, so the bob animation never clips.
- **No blur or filters.** The desktop renderer (Qt) ignores them. Make glows with radial gradients that fade to transparent.
- **No text.** Labels and speech bubbles belong to the UI.
- The art supplies **parts, not frames**. Bobbing, blinking, glow and swirl are animated in code.
- Hide or delete the `guides` layer before exporting.
- Export as **Plain SVG** (Inkscape) or a standard SVG export (Figma).

## States (how the app combines layers)

| State | Layers shown |
|---|---|
| idle | body, core (dim), highlight, outline, eyes-open, mouth-smile |
| noticing | + core brighter, core-icon |
| thinking | core swirls; eyes-closed, mouth-neutral |
| suggestion | core amber glow, eyes-open, mouth-smile, cheeks |
| talking | mouth-o and mouth-smile alternate |
| sleeping | eyes-closed, mouth-neutral, core faded |
| paused | paws, mouth-neutral, core off |

## Provenance
Concept: an AI-generated reference sheet (Google Gemini). The final art is drawn by Shreyas.
