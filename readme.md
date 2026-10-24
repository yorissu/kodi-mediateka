# Mediateka for Kodi

Unofficial Kodi add-on for [lrt.lt/mediateka](https://www.lrt.lt/mediateka), the Lithuanian public broadcaster's live TV, radio and recordings. No account needed. Works on Kodi 20 or newer (LibreELEC on a Raspberry Pi included) and is used with a TV remote.

## Installation

Done once, in Kodi, with the remote. Menu names are from Kodi 21 with the default skin.

1. **Allow outside add-ons:** Settings → System → Add-ons → switch on **Unknown sources** → Yes.
2. **Add the source:** Settings → File manager → **Add source** → `<None>` → type `https://yorissu.github.io/kodi-mediateka/` → OK → name it `mediateka` → OK.
   (Tip: the Kore or Yatse phone apps let you type this on your phone.)
3. **Install the repository:** Add-ons → add-on browser (box icon, top left) → **Install from zip file** → `mediateka` → `repository.mediateka-….zip`.
4. **Install the add-on:** add-on browser → **Install from repository** → Mediateka repository → Video add-ons → **Mediateka** → Install → OK.

## Usage

- **Live:** the TV channels (Televizija, Plius, Lituanica) and the radio stations (Radijas, Klasika, Opus, Radijas 100). The main TV channel has closed captions (Kodi's subtitle button).
- **Newest:** the newest video recordings from all shows.
- **TV shows / Radio shows:** the website's A-Z index. **On air now** lists the shows with a current season; the letters list all of them. A show with several seasons opens on its seasons, plus **All recordings**.
- **Categories:** the website's themes (Aktualijos, Filmai, Gyvenimas, Vaikams...).
- **Archive:** the heritage collection and recordings by decade, back to the 1950s.
- **Search:** Search → New search. Matching TV and radio shows come first (marked `[show]`), then recordings. Recent searches are kept; remove one from its context menu.
- **Watched and resume:** Kodi keeps them itself, like for files in its library.
- **Settings:** items per page, clearing the search history and the cache, debug logging.
- **Updates:** Kodi installs new releases automatically.
- **Something fails:** turn on **Debug logging** in the add-on's settings, try again and check the Kodi log for lines starting with `[plugin.video.mediateka]`. Some broadcasts are limited to Lithuania; the add-on says so when the website refuses one.

## How it works

The add-on uses the same JSON endpoints that the website's own players and pages use (search, show pages, media info, live stream addresses) and the website's A-Z show pages. They are not a documented API; [api.py](plugin.video.mediateka/resources/lib/api.py) lists them, and it is the place to look when a website update breaks something. Streams are plain HLS without DRM, so no InputStream add-on or Widevine is needed.

## Building

Needs only Python 3. In the project folder:

```bash
python build_zip.py
```

This writes `dist/` with two zips and `SHA256SUMS`:

- `plugin.video.mediateka-<version>.zip`, the add-on. Install it on a Kodi with **Install from zip file** to try it.
- `repository.mediateka-<version>.zip`, the repository add-on that points Kodi at the update site.

**Versions.** The add-on's version comes from the git tag; `addon.xml` only holds a placeholder.

- `python build_zip.py --tag v0.3.0` builds version `0.3.0`, exactly what the release will contain.
- `python build_zip.py --tag v0.3.0-rc.2` builds the pre-release `0.3.0~rc.2`. Kodi needs the `~` to sort it below `0.3.0`; the tag and the zip's file name (`…-0.3.0-rc.2.zip`) never have it.
- Without `--tag` the version comes from git (`0.3.1~dev2` = 2 commits after v0.3.0), or is `0.0.0~dev` without git.

The repository add-on keeps its own version (`repository.mediateka/addon.xml`, `1.0.0`), raised only when the update site's address changes.

`icon.png` and `license.txt` exist once, next to this readme; the build copies them into both zips.

## Testing

```bash
python -m unittest discover -s tests
```

The tests run without Kodi and without network: small stand-ins for Kodi's modules (`tests/kodi_stubs/`) and a fake website server with made-up data (`tests/helpers.py`).

## CI and releases

GitHub Actions does the rest:

- **CI** (`.github/workflows/ci.yml`), on every push to main and every pull request: runs the tests, builds the zips and runs Kodi's official add-on checker on the built add-on.
- **Release** (`.github/workflows/release.yml`), on pushing a tag:

  ```bash
  git tag v1.0.0
  git push origin v1.0.0
  ```

  It runs the tests, builds the zips with the tag's version and creates the GitHub release with the zips and `SHA256SUMS`. For a normal release (`vX.Y.Z`) it also rebuilds the Kodi repository site on GitHub Pages (`build_repo.py`), so every Kodi gets the update. A pre-release (`vX.Y.Z-<suffix>`) only becomes a GitHub pre-release: install its zip by hand to test it.
- **Pulling a broken release:** delete it on GitHub, then Actions → Release → **Run workflow** to rebuild the site without it.
- **One-time setup** of the GitHub repo:
  1. Settings → Pages → Source: **GitHub Actions**.
  2. Settings → Environments → **github-pages** → Deployment branches and tags → **Add deployment branch or tag rule** → Ref type **Tag**, pattern `v*`. Without it releases fail at the Pages step.

## License

MIT. Unofficial, not affiliated with the broadcaster. Some broadcasts are only available in Lithuania.
