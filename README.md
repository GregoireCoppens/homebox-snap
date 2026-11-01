# homebox-snap

Quick photo intake for [Homebox](https://github.com/sysadminsmedia/homebox) (v0.27+) from your phone.

Set a **location and tags once**, then for each item: tap the shutter a few times, type a name,
adjust the quantity, optionally print the label, **Save**. Location and tags stay set for the next item.

- Mobile web app (Dutch UI), installable on the home screen
- Each person logs in with their own Homebox account; the session lives in an HttpOnly cookie
- Photos are resized on the phone (max 1600 px) before upload; the first photo becomes the primary image
- Label printing uses Homebox's own label printer setup (`/labelmaker/item/{id}?print=true`)
- Remembers location, tags, item type and the print toggle per user on that device

## Run it on the NAS

```sh
cd /volume1/docker
git clone git@github.com:GregoireCoppens/homebox-snap.git && cd homebox-snap
cp .env.example .env            # check HOMEBOX_NETWORK and HOMEBOX_URL (below)
docker compose up -d --build
```

Find the right values for `.env`:

```sh
docker network ls | grep -i inventory                    # -> HOMEBOX_NETWORK
docker ps --format '{{.Names}}\t{{.Ports}}' | grep -i homebox   # container name; Homebox listens on 7745 inside
```

`HOMEBOX_URL` is `http://<homebox container or service name>:7745`.

## Caddy + DNS

Add a site for `snap.coppensvonck.eu` that proxies to the NAS on `SNAP_PORT`, in the same style as the
other services in your Caddyfile, for example:

```
snap.coppensvonck.eu {
    reverse_proxy 192.168.2.95:3105
}
```

Then a UniFi local DNS record `snap.coppensvonck.eu` → Caddy (192.168.2.250), and a tile on the
home.coppensvonck.eu landing page.

The camera only works over HTTPS, which Caddy provides.

## On the phone

Open `https://snap.coppensvonck.eu`, log in with your Homebox account, allow the camera, then
**Add to Home Screen** (Safari: share button; Chrome: ⋮ menu).

## Development

```sh
pip install -r requirements.txt pytest
PYTHONPATH=. pytest -q
```
