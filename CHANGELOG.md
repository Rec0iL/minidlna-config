# Changelog

## Unreleased

### Added

- A **Problems** view. It reads minidlna's log and groups it into a short
  list of things worth acting on, each with a likely cause: files that could
  not be parsed (grouped by reason and location), folders that were not
  accessible, requests refused for using a hostname, and stale items clients
  asked for. A dot on the tab flags warnings in the current run.
- A **Library check**: compares every folder with minidlna's index and sorts
  the differences into unreadable files, files that were never picked up, and
  index entries whose file has gone.
- A filtered, searchable **log viewer** that hides routine messages.
- A **Devices** view: connected clients as minidlna classified them, their
  addresses, live streaming rate and data sent, plus devices that only
  announced themselves.
- A **Settings** view for server name, port, network interface, announce
  interval, strict DLNA, file watching, the library top level, folder merging,
  symbolic links, album art names and database/log locations. Only changed
  keys are written; risky ones ask first.
- **Restore a previous version**: browse the saved copies of the configuration
  with a diff against the current file, and roll back to one.
- **Missing-drive handling**: when a folder minidlna skipped at start-up
  appears, a banner offers to rebuild the library (or does so automatically if
  enabled). Folders that vanish while the server runs are flagged.
- A **tray icon** with status, restart/rebuild actions and notifications for
  finished scans, drives coming and going, and the server stopping. It can
  keep the app running when the window is closed.
- Ctrl+Q quits for real, even when closing would hide to the tray.

- A **scan progress bar** in the header while minidlna is indexing, showing
  how many files are indexed out of an estimate of how many there will be.
  minidlna reports no progress itself, so the total is counted from the
  configured folders and is approximate; the bar is held short of 100% until
  the scan really ends.

### Fixed

- **Open web interface** used `localhost`, which minidlna rejects as a
  suspected DNS-rebinding attack, so it opened an error page. It now uses
  `127.0.0.1`.
- Table headers in the Library view were drawn as an unstyled white bar.
- After rebuilding the library the folder list stayed on "nothing indexed"
  (or a stale count) and the Library view was not redrawn. The index was read
  once, 900 ms after the rebuild started, when the new database was still
  almost empty. The window now follows the scan, updates counts live as files
  are indexed, and redraws both views when it finishes.

## 1.1.0

### Added

- A **Library** view showing what minidlna has actually indexed: totals per
  media type, and a searchable, sortable list of every file with its length,
  resolution and size. Double-clicking a row opens its folder.
- Each folder in the list now shows how many files are indexed underneath it
  and how much they take up, or says so when nothing is indexed - which
  distinguishes a folder that is shared but empty from one that is working.
- The Library view offers a rebuild when the media database is missing or
  empty, and reports files indexed outside the configured folders.

minidlna's index is opened read-only and is never written to.

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
