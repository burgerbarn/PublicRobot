# Media on bigbuddy's screen (owner OK 2026-09-28: "copy and host a modified container on big buddy")

- `jukebox/` = copy of the owner's BUDDY JUKEBOX (server `~/openwebui-stack/jukebox`, Flask proxy to Plex),
  modified (marked `[bigbuddy copy]`):
  - artist search: `/api/search` returns exact-artist matches first ("the beatles" == "beatles"), then titles
  - remote control for Buddy: `POST /api/remote {cmd: play|shuffle|pause|resume|next|prev|stop, arg}`,
    page polls `/api/remote/poll`; page reports `/api/remote/state` (now playing)
  - TV layout (≥1200 px, landscape, or `?tv`): LCD | track list | art on top 2/3, full-width spectrum bottom 1/3
- Runs as podman quadlet `buddy-jukebox.container` (here; installed at ~/.config/containers/systemd/) on
  127.0.0.1:8281. Plex token: ~/media-apps/plex_token → ~/media-apps/jukebox.env (0600), written by the owner
  from server's .env via a pipe; never in the repo or the chat. PLEX_URL http://192.168.1.122:32400.
- Rebuild: `cd ~/media-apps && podman build -t localhost/buddy-jukebox:latest jukebox && systemctl --user restart buddy-jukebox`
- Screen: kiosk Firefox (own profile ~/.buddy-media-firefox, transient unit `buddy-screen`, PULSE_SINK=HDMI,
  WebDriver BiDi on 127.0.0.1:9222) driven by `voice/media_screen.py`. Pointer hides after 3 s idle and returns on
  movement (both apps; an always-hidden pointer was a bad idea). Video page TV layout (≥1200 px landscape, owner design):
  picture 100 % wide x 77 vh on top (owner); bottom = [surf/filter/strength/frame] [library] [search + list].
  Movies start in this page view; `fullscreen {on}` remote command (video: plain fullscreen; jukebox: LCD
  fullscreen) — Buddy tool `fullscreen` ("go fullscreen" / "exit fullscreen").
- `video/` = copy of BUDDY VIDEO (server `~/openwebui-stack/video`), quadlet `buddy-video.container`,
  127.0.0.1:8282, `video.env` token. Changes `[bigbuddy copy]`: TV-set frame OFF by default (`frame` command
  toggles), per-style effect strength (crt 70, vhs 100, film8mm 90, seccam 85, gameboy 100 %; up to 150 % by
  voice; shared 35 % made VHS invisible), remote control (play {movie}, random {channel_index}, pause, resume,
  stop, style {name, strength}, frame {on}), stuck-stream retry after 8 s, own Plex client id, Plex 4xx/5xx
  reasons logged (no token). /api/random wants the channel INDEX (0-3), not the Plex section id; a plain random
  pick skips clips < 20 min ("Family" = home-video clips) and tries other channels; an explicit channel may return clips.
- Remote command cursor: poll returns a boot id; the page resets its cursor when the container restarts (else
  new commands looked "old" and were ignored).
- Voice: Buddy tools play_music / play_movie / media_control / set_video_style / now_playing; music pauses while
  you talk to Buddy and resumes after (unless the request changed the music).

## 2026-09-29: albums, playlists, token hygiene (jukebox copy)
- `/api/search` order: exact artist (shuffled, then title matches), else exact album (track order; "ALBUM by ARTIST"
  narrows), else exact playlist (playlist order), else title matches. Trailing "album"/"playlist" forces the kind.
  Names compare loosely (case, punctuation, leading "the"). `/api/playlists`: unique non-empty audio playlist names
  (Plex keeps one set of automatic playlists per music library); duplicate names play the largest copy, falling back
  to the next when Plex 500s on one (seen: an automatic "Fresh ❤️" copy).
- Plex token now goes in the `X-Plex-Token` header, not the URL, for all API calls (both apps) and the jukebox audio/art
  proxies, so request errors can't print it (an HTTPError had logged the full URL incl. token). Video's HLS
  `/api/plexproxy` still passes it as a param (never logged). Verified after the change: search, playlists, audio stream
  (206 FLAC), art, video init/search; 0 token/traceback lines in the logs.

- 2026-10-01 [bigbuddy copy]: remote cmd `album` = playAlbumFromCurrent (current song's album from track 1);
  album end -> shuffleAndPlay (was: stop). Voice tool play_album (media_screen.play_album).
