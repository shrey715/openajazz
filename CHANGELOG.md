# Changelog

## 0.5.0

- New desktop app (GTK 4 + libadwaita) for people who'd rather not keep a browser tab or a terminal open. It has everything the web app has: the live keyboard, the effect gallery, per-key painting with presets, remaps, macros, music sync and settings.
- Get it as a Flatpak from the release page (`flatpak install --user openajazz-0.5.0.flatpak`). It shares saved settings with the CLI and TUI.
- Music sync can listen to the microphone as well as to what's playing.
- `openajazz gui` starts the desktop app from the CLI if GTK is installed.

## 0.4.1

- Music sync: pick a single colour right in the music section (web and TUI), with a colour picker and quick swatches. It changes live while music is playing, and no longer borrows the per-key brush.
- When numpy is missing, the error now shows the exact command for uv, pipx or pip (`uv tool install --upgrade 'openajazz[music]'`). The AppImage already includes it.

## 0.4.0

The project is now **openajazz** (it was ak820ctl), so other AJAZZ keyboards can join later. The command is `openajazz`, the web app lives at [openajazz.vercel.app](https://openajazz.vercel.app), and your saved settings and presets move over by themselves.

- Music sync can run without a terminal: `openajazz music --background`, stopped with `openajazz music --stop`.
- `openajazz autostart music` starts music sync at login (systemd user service, restarts if it crashes). `openajazz autostart off` undoes it.
- Web app: calmer, flat controls, proper icons everywhere, and a cleaner top bar with a battery gauge.
- The udev rule is now `70-openajazz.rules`. Run `openajazz udev --install` once; you can delete the old `70-ajazz-ak820.rules`.

## 0.3.1

- Effect previews now match the real keyboard:
  - Bidirectional ejection: a key press fires left and right along that key's row.
  - Jiangshan picture scroll: a key press sends its whole column sideways.
  - Kaleidoscope: rings ripple out from the centre.
  - Cross operation: two waves come in from the edges and pass each other.
  - Top three, bottom three: the top rows sweep right while the bottom rows sweep left.
  - Rain falling: streaks run sideways.
  - Galloping horse: one row lights up at a time, left to right.
- Effects that react to typing get simulated key presses in the preview, and in the web app you can click keys to fire them.
- The keyboard is drawn on a proper 75% grid, so rows line up.
- Web app: centred on wide screens, smaller keyboard so the controls fit, and "Your design" explains itself (paint in Per-key, press Upload design, then pick the tile).

## 0.3.0

- Web app: control the keyboard from Chrome, Edge or Opera, nothing to install.
- Music sync (CLI, TUI and web): lights follow whatever is playing.
- Lights off and on, with your previous lighting restored.
- Effect previews in the TUI and the web app.
- CLI: effect and colour names (`light breathing -c cyan`), `--json`, `keys`, `restore`, `udev --install`.
- TUI: music tab, undo and redo in the painter, live painting on the keyboard.
- AppImage on every release.
- Hardware findings from [prasoondev/ajazz-linux](https://github.com/prasoondev/ajazz-linux): the colour palette, battery states, live frames, and a udev rule that only exposes the settings interface.

## 0.2.0

- Reworked TUI: paint and drag per-key colours, pattern fills, presets, remaps and macros picked on a keyboard map, and settings remembered between runs.

## 0.1.0

- First release: protocol reverse engineered from the Windows driver, with a CLI and a TUI.
