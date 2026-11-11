#!/usr/bin/env bash
# Build openajazz-x86_64.AppImage: a portable Python 3.12 (niess/python-appimage)
# with the openajazz wheel and the music extra installed into it.
#
#   packaging/appimage/build.sh            # builds dist/openajazz-<version>-x86_64.AppImage
#
# Needs: curl, python3 (with build), and either FUSE or APPIMAGE_EXTRACT_AND_RUN=1.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PYTHON="${PYTHON:-python3}"
case "$PYTHON" in /*) ;; */*) PYTHON="$PWD/$PYTHON" ;; esac  # absolute, keep venv symlinks
HERE="$ROOT/packaging/appimage"
WORK="$ROOT/build/appimage"
PY_URL="https://github.com/niess/python-appimage/releases/download/python3.12/python3.12.15-cp312-cp312-manylinux2014_x86_64.AppImage"
TOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
VERSION="$(python3 -c "import re; print(re.search(r'__version__ = \"(.+?)\"', open('$ROOT/openajazz/__init__.py').read()).group(1))")"

rm -rf "$WORK" && mkdir -p "$WORK" "$ROOT/dist"
cd "$WORK"

echo "• building wheel"
if command -v uv >/dev/null; then
  uv build --quiet --wheel --out-dir "$WORK/wheel" "$ROOT"
else
  "$PYTHON" -m build --wheel --outdir "$WORK/wheel" "$ROOT" >/dev/null
fi
WHEEL="$(ls "$WORK"/wheel/openajazz-*.whl)"

echo "• fetching portable python + appimagetool"
curl -fsSLo python.AppImage "$PY_URL"
curl -fsSLo appimagetool "$TOOL_URL"
chmod +x python.AppImage appimagetool
./python.AppImage --appimage-extract >/dev/null
mv squashfs-root AppDir

echo "• installing openajazz $VERSION"
AppDir/opt/python3.12/bin/python3.12 -m pip install --quiet --no-warn-script-location \
  --no-compile "${WHEEL}[music]"

# swap the python launcher for ours
rm -f AppDir/*.desktop AppDir/python.png AppDir/.DirIcon
install -m755 "$HERE/AppRun" AppDir/AppRun
install -m644 "$HERE/openajazz.desktop" AppDir/openajazz.desktop
install -m644 "$HERE/openajazz.png" AppDir/openajazz.png
ln -s openajazz.png AppDir/.DirIcon
mkdir -p AppDir/usr/share/applications AppDir/usr/share/icons/hicolor/256x256/apps
cp AppDir/openajazz.desktop AppDir/usr/share/applications/
cp AppDir/openajazz.png AppDir/usr/share/icons/hicolor/256x256/apps/

# extracted python ships some 0700 folders; appimagetool rejects those
chmod -R a+rX AppDir

echo "• packing"
OUT="$ROOT/dist/openajazz-$VERSION-x86_64.AppImage"
ARCH=x86_64 ./appimagetool --no-appstream AppDir "$OUT" >/dev/null 2>&1 \
  || ARCH=x86_64 ./appimagetool --appimage-extract-and-run --no-appstream AppDir "$OUT"
echo "✓ $OUT ($(du -h "$OUT" | cut -f1))"
