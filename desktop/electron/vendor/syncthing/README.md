# Embedded Syncthing (Windows)

This folder is used by the desktop app to run an embedded Syncthing instance on Windows.

## Prepare binary

Run:

```powershell
npm run syncthing:prepare:win
```

To use another version:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\fetch-syncthing-win.ps1 -Version 1.27.12
```

This downloads `syncthing.exe` into:

`electron/vendor/syncthing/windows-amd64/syncthing.exe`

## Packaging

`electron-builder` copies the embedded binary to:

`resources/syncthing/syncthing.exe`

and the desktop app launches it automatically at startup (Windows only).

## License

Syncthing is licensed under MPL-2.0. Keep the upstream license text with distributed binaries.
