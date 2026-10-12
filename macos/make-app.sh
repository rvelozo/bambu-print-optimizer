#!/bin/sh
# Build "Slicer Optimizer.app" (default: in ~/Applications). Open it to get the home page,
# drop .3mf files on it, or pick it in Finder's Open With menu for a .3mf. It runs
# slicer-opt from this checkout, so rebuild it if you move the checkout.
#
#     macos/make-app.sh [DESTINATION_FOLDER]
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(dirname "$here")"
dest="${1:-$HOME/Applications}"
app="$dest/Slicer Optimizer.app"
id="local.slicer-optimizer.launcher"
plist="$app/Contents/Info.plist"
pb=/usr/libexec/PlistBuddy

if [ -e "$app" ] && [ "$($pb -c 'Print :CFBundleIdentifier' "$plist" 2>/dev/null || true)" != "$id" ]; then
  echo "make-app: $app exists and is not this launcher; not replacing it" >&2
  exit 1
fi
python=""
for candidate in "${PYTHON:-}" /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
  [ -n "$candidate" ] && command -v "$candidate" >/dev/null 2>&1 && { python="$candidate"; break; }
done

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
sed "s|__SLICER_OPT__|$repo/slicer-opt|" "$here/launcher.applescript" > "$work/launcher.applescript"
mkdir -p "$dest"
rm -rf "$app"
osacompile -o "$app" "$work/launcher.applescript"

$pb -c "Set :CFBundleName Slicer Optimizer" "$plist"
$pb -c "Add :CFBundleIdentifier string $id" "$plist"
$pb -c "Add :CFBundleShortVersionString string $(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$repo/slicer_optimizer/__init__.py")" "$plist"
$pb -c "Delete :CFBundleDocumentTypes" "$plist"
$pb -c "Add :CFBundleDocumentTypes array" "$plist"
$pb -c "Add :CFBundleDocumentTypes:0 dict" "$plist"
$pb -c "Add :CFBundleDocumentTypes:0:CFBundleTypeName string Bambu Studio project" "$plist"
$pb -c "Add :CFBundleDocumentTypes:0:CFBundleTypeRole string Viewer" "$plist"
$pb -c "Add :CFBundleDocumentTypes:0:LSHandlerRank string Alternate" "$plist"
$pb -c "Add :CFBundleDocumentTypes:0:LSItemContentTypes array" "$plist"
$pb -c "Add :CFBundleDocumentTypes:0:LSItemContentTypes:0 string com.bambulab.3mf" "$plist"
$pb -c "Add :CFBundleDocumentTypes:0:CFBundleTypeExtensions array" "$plist"
$pb -c "Add :CFBundleDocumentTypes:0:CFBundleTypeExtensions:0 string 3mf" "$plist"

if [ -n "$python" ] && "$python" "$here/render_icon.py" "$work/icon.png" 1024; then
  mkdir "$work/icon.iconset"
  for s in 16 32 128 256 512; do
    sips -z "$s" "$s" "$work/icon.png" --out "$work/icon.iconset/icon_${s}x${s}.png" >/dev/null
    d=$((s * 2))
    sips -z "$d" "$d" "$work/icon.png" --out "$work/icon.iconset/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns -o "$app/Contents/Resources/droplet.icns" "$work/icon.iconset"
  rm -f "$app/Contents/Resources/Assets.car"   # it holds the stock droplet icon, which would win
  $pb -c "Delete :CFBundleIconName" "$plist" 2>/dev/null || true
fi

codesign --force --sign - "$app" 2>/dev/null
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$app"
echo "Built $app"
