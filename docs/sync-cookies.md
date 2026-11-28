# `aiuse sync-cookies`: Cursor and Grok sessions for a headless box

CodexBar on Linux cannot read macOS browser or Keychain sessions, so `cursor`
and `grok` fail there ("needs a cursor.com Cookie header", "No available fetch
strategy for grok"). CodexBar has no command that prints a resolved cookie, so
aiuse does the extraction on the Mac and feeds `codexbar config set-cookie
--stdin` on the box.

```
Mac:  aiuse sync-cookies push --to grok-bot-box       # extract + ssh into the drop dir
Box:  aiuse sync-cookies ingest                       # apply + shred (also runs before every CodexBar collection)
Box:  aiuse sync-cookies status                       # when applied, when it expires
```

| Provider | Source on the Mac | Lifetime |
| --- | --- | --- |
| cursor | `cursor-access-token` in the login Keychain (written by `cursor-agent`), sent as `WorkosCursorSessionToken=<user>%3A%3A<jwt>` | JWT `exp`, about 60 days; refreshed by cursor-agent on the Mac |
| grok | `sso`/`sso-rw` cookies for grok.com from Chrome (needs the `chrome-refresh` extra); `--grok-profile` pins a profile, default is the longest-lived session | cookie expiry, months |

Cookie values never appear in argv, logs, or output. The drop file is `0660`,
atomically renamed, and shredded by ingest. Setup on the box: run
`scripts/box-cookie-drop-setup.sh` as root. A launchd template is in
`packaging/launchd/`. Re-running push every few hours is cheap and keeps a
renewed Cursor token current; after expiry CodexBar reports an auth error.
