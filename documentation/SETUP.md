# Setting up Paravent

The shortest way to a running table. Details are in the [README](../README.md).

You need the 5eTools data; Paravent ships no 5e content. Everything else is below.

## 1. Run it

**On your own machine** (needs [uv](https://docs.astral.sh/uv/)):

```bash
uv venv
uv pip install -e ".[dev]"
cp .env.example .env
```

In `.env`, set `PARAVENT_REFERENCE_DIR` to your 5eTools folder, the one holding `bestiary/`,
`spells/` and `class/`. Delete the `PARAVENT_IMAGE_DIR` line unless your pictures are
elsewhere than in `img/` next to that folder. Then:

```bash
./run.sh --lan
```

**Or on a NAS or server** (needs Docker). In a `.env` beside `docker-compose.yaml`:

```bash
PARAVENT_REFERENCE_DIR=/volume1/docker/paravent/5etools-data
PARAVENT_IMAGE_DIR=/volume1/docker/paravent/5etools-img
PARAVENT_LIBRARY_DIR=/volume1/docker/paravent/library
PARAVENT_BACKUP_DIR=/volume1/docker/paravent/backups
PUID=1026    # the owner of the library folder: `id <user>`
PGID=100
```

```bash
docker compose up -d --build
```

## 2. Set up the table

1. **Create the gamemaster** at `http://<host>:8000/setup`. The page closes once used.
2. **Check the data:** settings gear → *5eTools data*. It should report no errors.
3. **Create a world** under *Worlds* and declare its books. No books means all of them.
4. **Add players:** settings gear → *Players*.
5. **Players** open the same address on their phones and can add it to the home screen.

Inside the app, *Table* → **Getting started** (`/help/start`, `app/help/start.<lang>.md`) walks
a new gamemaster from here to the first fight.

## 3. Back up

`data/library/` (or `PARAVENT_LIBRARY_DIR`) holds everything you make, and nothing else does.
Set `PARAVENT_BACKUP_DIR` and `PARAVENT_BACKUP_AT=03:00` for daily zips, or use *Backups* under
the settings gear. The folder must lie outside the library and the corpus.

## Optional

- **Claude as co-author:** settings gear → *Claude (MCP)* → new key, then
  `claude mcp add --transport http paravent http://<host>:8000/mcp --header "Authorization: Bearer <key>"`.
- **Machine translation:** set `PARAVENT_TRANSLATE_API_KEY` (a Claude API key, paid per use).

## If something is wrong

| You see | It means |
|---|---|
| No monsters or spells | `PARAVENT_REFERENCE_DIR` is wrong; open `/health` for the count |
| Docker login fails, `PermissionError` in the log | `PUID:PGID` does not own the library folder |
| Login seems to do nothing | `PARAVENT_COOKIE_SECURE` is on without HTTPS |
| Phones cannot connect | Started without `--lan`, or a firewall blocks port 8000 |
