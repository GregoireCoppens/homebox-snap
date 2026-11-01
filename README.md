# homebox-snap

Quick photo intake for [Homebox](https://github.com/sysadminsmedia/homebox) (v0.27+) from your phone.

Set a **location and tags once**, then for each item: tap the shutter a few times, type a name,
adjust the quantity, **Save**. Location and tags stay set for the next item.

- Mobile web app (Dutch UI), installable on the home screen
- Each person logs in with their own Homebox account; the session lives in an HttpOnly cookie
- Photos are resized on the phone (max 1600 px) before upload; the first photo becomes the primary image
- Remembers location, tags and item type per user on that device

## Run it on the NAS

The NAS has no `git`, so clone with a throwaway git container and a read-only deploy key:

```sh
# once: a deploy key (add the .pub as a read-only Deploy key on GitHub)
sudo mkdir -p /volume1/docker/.keys && sudo chmod 700 /volume1/docker/.keys
sudo ssh-keygen -t ed25519 -N "" -C "nas homebox-snap" -f /volume1/docker/.keys/homebox-snap
sudo cat /volume1/docker/.keys/homebox-snap.pub

# clone (later: replace "clone git@...git" by "-C homebox-snap pull")
sudo docker run --rm -v /volume1/docker:/work -v /volume1/docker/.keys:/keys:ro -w /work \
  -e GIT_SSH_COMMAND="ssh -i /keys/homebox-snap -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/dev/null" \
  alpine/git clone git@github.com:GregoireCoppens/homebox-snap.git

cd /volume1/docker/homebox-snap
sudo docker compose up -d --build
```

Defaults match the NAS: network `inventory_default`, Homebox at `http://homebox:7745`
(override in a `.env` file, see `.env.example`).

## Caddy + DNS

Inside the existing `*.coppensvonck.eu { ... }` block of the Caddyfile, next to the other services:

```
    @snap host snap.coppensvonck.eu
    handle @snap {
        reverse_proxy homebox-snap:8000
    }
```

Reload: `sudo docker exec caddy caddy reload --config /etc/caddy/Caddyfile`.
Then a UniFi local DNS record `snap.coppensvonck.eu` → Caddy (192.168.2.250), and a tile on the
home.coppensvonck.eu landing page. The camera only works over HTTPS, which Caddy provides.

## On the phone

Open `https://snap.coppensvonck.eu`, log in with your Homebox account, allow the camera, then
**Add to Home Screen** (Safari: share button; Chrome: ⋮ menu).

## Development

```sh
pip install -r requirements.txt pytest
PYTHONPATH=. pytest -q
```
