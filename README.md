# NabiCat

![cat ico](web_app/static/nabicat.png)

A cozy collection of misc web apps [visit](https://nabicat.site)

## Setup

### Env

create a `.env` file in the root of the project with the following content:

* `FLASK_SECRET_KEY` - can be any random 24 char str

### Conda + Other Misc Reqs

* setup conda env - see `setup_server.sh:setup_conda`
* install `ffmpeg`
* install `terraform`

## Running

```bash
pip install -r requirements.txt
python -m web_app [--debug] [--port PORT]
```

## Testing

`python -m pytest`

### Playwright UI Tests

```bash
pip install playwright
playwright install
sudo $(which playwright) install-deps
pytest tests/ui/ # run ui tests in headless mode

# to see whats actually being tested
# pytest tests/ui/ --headed --slowmo 500
```


## Cloud Setup

register host .ssh creds as github ssh key

then run client side setup

`source setup_server.sh && run_client_side $CLOUD_PROVIDER`

CLOUD_PROVIDER is either aws or oci (aws doesn't work atm as the instance doesnt have enough storage)

register the generated ip address with your domain name
`export SERVER_IP_ADDR=$(terraform -chdir=terraform/$CLOUD_PROVIDER output server_ip_addr | sed 's/\"//g')`

it may take a while for the ip to be associated with the domain, but once it's done run the final step.
Make sure to replace the email with your own for certbot

`ssh nabicat.site -t "ssh-keyscan github.com >> ~/.ssh/known_hosts 2>/dev/null && git clone git@github.com:quitefrankli/nabicat.git && cd nabicat && source setup_server.sh && CERTBOT_EMAIL=your@email.com run_server_side"`


### Systemd Services

`update_server.sh` generates and manages two systemd units:

* `nabicat.service` — the gunicorn web app
* `meridian.service` — the Meridian LLM proxy used by LLM-backed app features

sudo systemctl status nabicat meridian

### Logs

stdout is captured by journald

```bash
journalctl -u nabicat -f      # for stdout/stderr from update_server.sh bash script
journalctl -u meridian -f     # live meridian logs
```

### Claude Login (first-time Meridian setup)

Meridian proxies requests to the `claude` CLI, which needs to be authenticated once on the server as the same user the service runs under (whoever ran `setup_server.sh`):

```bash
claude login
sudo systemctl restart meridian
```


### Misc

to bring down the server

`terraform -chdir=terraform/$CLOUD_PROVIDER destroy`

## Updating Server

a. simply push from main branch, force push also works too
b. on main branch run - `python scripts/api_helper.py update`
c. run on server - `bash update_server.sh`

## Renewing Cert

Renewal is automatic. `setup_server.sh` installs certbot via apt, which ships a `certbot.timer` systemd unit that runs `certbot renew` twice daily. Pre/post hooks (stop/start nginx) are stored in `/etc/letsencrypt/renewal/nabicat.site.conf` and run on each renewal.

To verify:

```bash
systemctl list-timers certbot.timer
sudo certbot renew --dry-run
```

To force a manual renewal:

```bash
sudo certbot renew --force-renewal
```
