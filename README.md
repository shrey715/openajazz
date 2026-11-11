# openajazz

Control **AJAZZ keyboards** without the Windows app: lighting effects, per-key RGB, key remaps, macros, music sync, sleep and battery. Works over the USB cable or the 2.4G dongle.

![the tui lighting tab with a live effect preview](https://raw.githubusercontent.com/shrey715/openajazz/main/docs/tui-lighting.svg)

> [!WARNING]
> Unofficial. Not affiliated with AJAZZ. The protocol was reverse engineered from the vendor's Windows driver, and the lighting, colours, per-key RGB and live frames have been confirmed on real keyboards. It only sends the settings packets the official app sends and never touches firmware. Use at your own risk; `openajazz factory-reset` puts the keyboard back to stock.

## Supported keyboards

| Keyboard | Wired | 2.4G dongle | Bluetooth |
|---|---|---|---|
| AK820 Max Plus (tri-mode RGB) | yes | yes | no (the keyboard doesn't expose settings over Bluetooth) |

Have another AJAZZ board? Open an issue with the output of `openajazz devices` and the name of the vendor driver package; most of them share this protocol.

## Get it

Pick one:

| | |
|---|---|
| **Web, no install** | open **[openajazz.vercel.app](https://openajazz.vercel.app)** in Chrome, Edge or Opera on a computer |
| **Desktop app (Flatpak)** | download `openajazz-<version>.flatpak` from [releases](https://github.com/shrey715/openajazz/releases/latest), then `flatpak install --user openajazz-*.flatpak` |
| **pipx / uv** | `pipx install 'openajazz[music]'` or `uv tool install 'openajazz[music]'` |
| **AppImage** | grab it from [releases](https://github.com/shrey715/openajazz/releases/latest), `chmod +x`, run |

### Linux permissions

Once, so you don't need sudo (the web app needs this too):

```sh
openajazz udev --install      # then replug the keyboard or dongle
```

It only opens the keyboard's settings interface to your user, never the typing one.

## Use it

```sh
openajazz                          # full-screen TUI
openajazz gui                      # desktop app (or launch "openajazz" from your app menu with the Flatpak)
openajazz status                   # battery
openajazz light breathing -c cyan  # effect + colour (openajazz modes lists them)
openajazz lights off               # and `on` brings your lighting back
openajazz custom -a 101010 w=red a=red s=red d=red
openajazz remap capslock esc
openajazz macro f12 +lctrl c -lctrl
openajazz music --background       # lights follow what's playing, terminal can close
openajazz autostart music          # ...and start that at every login
openajazz --help                   # everything else
```

Coming from ak820ctl? Same commands under the new name; your saved settings and presets move over on first run.

## What doesn't work (yet)

- Bluetooth: plug in the cable or dongle to change settings.
- Phones: browsers on phones don't support WebHID.
- Firmware updates and mouse-button assignments aren't supported.

More detail: [CHANGELOG](https://github.com/shrey715/openajazz/blob/main/CHANGELOG.md) · [PROTOCOL.md](https://github.com/shrey715/openajazz/blob/main/PROTOCOL.md) · [issues](https://github.com/shrey715/openajazz/issues)

## Credits

Hardware test results, the colour palette, live-frame behaviour and the effect previews come from [prasoondev/ajazz-linux](https://github.com/prasoondev/ajazz-linux) (MIT).

MIT licensed. "AJAZZ" and "AK820" are trademarks of their owner, used here only to say what hardware this is for.
