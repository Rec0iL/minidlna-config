# Changelog

## 1.0.0

A complete rewrite, replacing the original Tkinter implementation.

### Fixed

- Folders set to more than one media type (`media_dir=PV,/path`) raised
  `ValueError` while parsing. A bare `except` swallowed it, so those folders
  disappeared from the list and were erased from the file on the next save.
- Saving rebuilt the `media_dir` block at the end of the file instead of where
  it was, because the insertion point was searched for in a list the entries
  had already been removed from.
- The configuration was written with a plain truncating write, so an
  interrupted save could leave a destroyed `minidlna.conf`.
- Restarting fell back to `killall minidlnad` and reported success, which
  stopped the server rather than restarting it.
- The sudo password was collected in an application dialog and piped to
  `sudo -S`.

### Changed

- Rewritten with PySide6: animated backdrop, glass panels, media-type chips,
  inline path editing and non-blocking notifications.
- Folders can be added by dragging them onto the window, or several at a time
  from the picker, which lists mounted drives in its sidebar.
- Media types are guessed from a folder's contents when it is added, and stay
  editable per folder.
- **Apply** saves and restarts in one action, behind a single authorisation
  prompt.
- Configuration writes are atomic and keep timestamped backups.
- Privileges come from polkit via `pkexec`; the application never handles the
  password and refuses to run as root.
- Only `media_dir=` lines are rewritten. Loading and saving an otherwise
  unchanged file reproduces it byte for byte.

### Added

- Warnings for folders that do not exist, or that the account minidlna runs as
  cannot read - the usual reason a folder silently stays empty.
- Live service status, with start, stop, restart and library rebuild.
- An installer with a desktop entry and icon, and an uninstaller.
- A test suite that runs on the standard library alone.
