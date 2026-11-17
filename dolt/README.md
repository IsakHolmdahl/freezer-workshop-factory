# Dolt server for Beads

Dolt SQL server holding the Beads (`bd`) databases of all projects, adapted from
upstream Scion's Dolt compose setup (Dolt 2.0.7, private-before-listener
initialisation, checksum-pinned binary).

Files: `Dockerfile.dolt`, `dolt.py` (init + health + exec of `dolt sql-server`),
`lib.py` (helpers used by `dolt.py`).

## Setup

`./setup.py` does all of this. By hand:

1. Put `DOLT_DB_PASSWORD` and `DOLT_ROOT_PASSWORD` in `.env.secrets` (see `.env.secrets.template`),
   e.g. `openssl rand -hex 24`. They are applied on the first start only.
2. `docker compose up -d --build dolt`. Listens on `127.0.0.1:${DOLT_PORT:-3307}`
   (database `${DOLT_DATABASE:-beads}`, user `${DOLT_DB_USER:-beads}`).
   The image downloads the checksum-pinned Dolt release itself (linux/amd64).
3. Register it in the Scion Hub: `scion hub env set --scope hub DOLT_URL mysql://beads@127.0.0.1:3307`
   and `scion hub secret set --scope hub DOLT_DB_PASSWORD <password>`.
4. Optional: `scripts/init-beads.sh` creates a shared beads workspace against this server.

The database name and user are fixed when the volume is first initialised. If
you change `DOLT_DATABASE` / `DOLT_DB_USER` later, Dolt refuses to start
("identity mismatch"): set them back, or remove the volume (this deletes all
Beads data).

State lives in the `dolt-data` volume. Never `docker compose down -v`. A partial
initialisation fails closed; repair offline.
